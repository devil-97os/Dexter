from __future__ import annotations

import os
import sqlite3
import threading
from pathlib import Path

from . import policy

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "index.db"

# Directory and secret rules live in policy.py so every component agrees.
SKIP_DIRS = policy.SKIP_DIRS

TEXT_EXTENSIONS = {
    ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx",
    ".c", ".h", ".cpp", ".hpp", ".cc", ".rs", ".go",
    ".java", ".kt", ".cs", ".html", ".css", ".scss",
    ".json", ".jsonl", ".yaml", ".yml", ".toml", ".xml",
    ".md", ".txt", ".rst", ".ini", ".cfg", ".conf",
    ".ps1", ".bat", ".cmd", ".sh", ".sql", ".cmake",
    ".make", ".gitignore", ".dockerfile", ".env.example",
}

MAX_FILE_BYTES = 1_000_000
DB_LOCK = threading.RLock()


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.execute("""
        CREATE TABLE IF NOT EXISTS files (
            path TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            extension TEXT NOT NULL,
            size INTEGER NOT NULL,
            modified REAL NOT NULL,
            content TEXT NOT NULL
        )
    """)
    con.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS files_fts
        USING fts5(path, name, content, tokenize='unicode61')
    """)
    return con


def should_index(path: Path) -> bool:
    if path.is_symlink() or not path.is_file():
        return False
    if policy.is_secret(path.name):
        return False
    if path.suffix.lower() not in TEXT_EXTENSIONS:
        return False
    try:
        return path.stat().st_size <= MAX_FILE_BYTES
    except OSError:
        return False


def extract_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        try:
            return path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            return None


def index_file(con: sqlite3.Connection, path: Path) -> bool:
    try:
        stat = path.stat()
        key = str(path.resolve())
        old = con.execute(
            "SELECT size, modified FROM files WHERE path = ?", (key,)
        ).fetchone()

        if old and old == (stat.st_size, stat.st_mtime):
            return False

        content = extract_text(path)
        if content is None:
            return False

        con.execute("DELETE FROM files_fts WHERE path = ?", (key,))
        con.execute("""
            INSERT OR REPLACE INTO files
            (path, name, extension, size, modified, content)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            key, path.name, path.suffix.lower(),
            stat.st_size, stat.st_mtime, content,
        ))
        con.execute(
            "INSERT INTO files_fts(path, name, content) VALUES (?, ?, ?)",
            (key, path.name, content),
        )
        return True
    except OSError:
        return False


def prune_missing(con: sqlite3.Connection, root: Path) -> int:
    """Drop index rows under root whose file no longer exists on disk."""
    prefix = str(root)
    rows = con.execute(
        "SELECT path FROM files WHERE substr(path, 1, ?) = ?",
        (len(prefix), prefix),
    ).fetchall()
    gone = [r[0] for r in rows if not os.path.exists(r[0])]
    for key in gone:
        con.execute("DELETE FROM files_fts WHERE path = ?", (key,))
        con.execute("DELETE FROM files WHERE path = ?", (key,))
    return len(gone)


def _index_folder_locked(folder: str | Path, max_files: int | None = None,
                         cancel_event=None, on_progress=None) -> dict:
    """Index text files under folder. max_files caps files scanned (None = no cap)."""
    root = Path(folder).expanduser().resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"Folder does not exist: {root}")

    con = connect()
    counts = {"scanned": 0, "indexed": 0, "skipped": 0, "removed": 0,
              "capped": False}

    try:
        for current, dirs, files in os.walk(root, followlinks=False):
            if cancel_event and cancel_event.is_set():
                counts["cancelled"] = True
                break
            dirs[:] = [
                d for d in dirs
                if not policy.skip_dir(d)
                and not (Path(current) / d).is_symlink()
            ]

            for filename in files:
                if max_files is not None and counts["scanned"] >= max_files:
                    counts["capped"] = True
                    break

                path = Path(current) / filename
                counts["scanned"] += 1

                if should_index(path):
                    if index_file(con, path):
                        counts["indexed"] += 1
                else:
                    # A path that is now excluded must not leave an old
                    # searchable excerpt behind in the persistent FTS index.
                    key = str(path.resolve())
                    con.execute("DELETE FROM files_fts WHERE path = ?", (key,))
                    con.execute("DELETE FROM files WHERE path = ?", (key,))
                    counts["skipped"] += 1

                if counts["scanned"] % 100 == 0 and on_progress:
                    on_progress(dict(counts))
                if cancel_event and cancel_event.is_set():
                    counts["cancelled"] = True
                    break

            con.commit()
            if counts["capped"]:
                break
        if not counts.get("cancelled") and not counts["capped"]:
            counts["removed"] = prune_missing(con, root)
        con.commit()
    finally:
        con.close()

    return counts


def index_folder(folder: str | Path, max_files: int | None = None,
                 cancel_event=None, on_progress=None) -> dict:
    """Serialize content-index writes while keeping reads available between commits."""
    with DB_LOCK:
        return _index_folder_locked(folder, max_files, cancel_event, on_progress)
