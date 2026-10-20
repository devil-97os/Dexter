"""Fast file-NAME index for all ordinary files in the allowed roots.

Scanning walks only the allowed roots and prunes OS / junk / hidden / link
directories before descending, so it never explores Windows, Program Files,
AppData or other drives. Lookup is an FTS5 query over names and paths.
Content search stays in the separate content index (index.db), which is
filled per project on demand.
"""
from __future__ import annotations

import math
import os
import re
import sqlite3
import time
from datetime import datetime

from . import policy

DB_PATH = policy.ROOT / "data" / "paths.db"

CODE_EXTENSIONS = {".py", ".pyi", ".c", ".h", ".cpp", ".hpp", ".cc", ".rs",
                   ".go", ".java", ".kt", ".cs", ".js", ".jsx", ".ts", ".tsx"}
PROJECT_MARKERS = {".git", "pyproject.toml", "package.json", "cmakelists.txt",
                   "cargo.toml", "setup.py", "requirements.txt", "makefile",
                   "go.mod", "pom.xml", "build.gradle"}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS paths (
    path TEXT PRIMARY KEY, name TEXT NOT NULL, ext TEXT NOT NULL,
    is_dir INTEGER NOT NULL, is_project INTEGER NOT NULL,
    size INTEGER NOT NULL, mtime REAL NOT NULL, depth INTEGER NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS paths_fts USING fts5(
    name, path, content='paths', content_rowid='rowid', tokenize='unicode61'
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TRIGGER IF NOT EXISTS paths_ai AFTER INSERT ON paths BEGIN
    INSERT INTO paths_fts(rowid, name, path) VALUES (new.rowid, new.name, new.path);
END;
CREATE TRIGGER IF NOT EXISTS paths_ad AFTER DELETE ON paths BEGIN
    INSERT INTO paths_fts(paths_fts, rowid, name, path)
    VALUES ('delete', old.rowid, old.name, old.path);
END;
CREATE TRIGGER IF NOT EXISTS paths_au AFTER UPDATE ON paths BEGIN
    INSERT INTO paths_fts(paths_fts, rowid, name, path)
    VALUES ('delete', old.rowid, old.name, old.path);
    INSERT INTO paths_fts(rowid, name, path) VALUES (new.rowid, new.name, new.path);
END;
"""


def connect(fresh: bool = False) -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    if fresh:
        con.executescript(
            "DROP TABLE IF EXISTS paths_fts; DROP TABLE IF EXISTS paths;"
        )
    con.executescript(_SCHEMA)
    return con


def is_built() -> bool:
    if not DB_PATH.exists():
        return False
    con = connect()
    try:
        return con.execute("SELECT 1 FROM paths LIMIT 1").fetchone() is not None
    finally:
        con.close()


def _hidden(entry: os.DirEntry) -> bool:
    try:
        attrs = getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0)
    except OSError:
        return True
    return bool(attrs & 0x6)  # Windows HIDDEN | SYSTEM


def _is_link(entry: os.DirEntry) -> bool:
    if entry.is_symlink():
        return True
    junction = getattr(entry, "is_junction", None)
    return bool(junction and junction())


def scan(roots=None, on_progress=None, cancel_event=None) -> dict:
    """Incrementally refresh names, retaining old records on partial roots."""
    roots = list(roots) if roots else policy.load_roots()
    if not roots:
        raise RuntimeError("No allowed roots exist. Edit data/roots.json.")

    started = time.time()
    con = connect()
    counts = {"dirs": 0, "files": 0, "projects": 0}
    batch: list[tuple] = []
    inaccessible: list[str] = []
    inaccessible_count = 0
    con.execute("BEGIN IMMEDIATE")
    con.execute("CREATE TEMP TABLE IF NOT EXISTS scan_seen (path TEXT PRIMARY KEY)")
    con.execute("DELETE FROM scan_seen")

    def flush() -> None:
        con.executemany(
            """INSERT INTO paths VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(path) DO UPDATE SET
                 name=excluded.name, ext=excluded.ext,
                 is_dir=excluded.is_dir, is_project=excluded.is_project,
                 size=excluded.size, mtime=excluded.mtime, depth=excluded.depth
               WHERE paths.name IS NOT excluded.name
                  OR paths.ext IS NOT excluded.ext
                  OR paths.is_dir IS NOT excluded.is_dir
                  OR paths.is_project IS NOT excluded.is_project
                  OR paths.size IS NOT excluded.size
                  OR paths.mtime IS NOT excluded.mtime
                  OR paths.depth IS NOT excluded.depth""",
            batch,
        )
        con.executemany("INSERT OR IGNORE INTO scan_seen(path) VALUES (?)",
                        [(row[0],) for row in batch])
        batch.clear()
        if on_progress:
            on_progress(dict(counts))

    try:
        for root in roots:
            root = str(root)
            root_complete = True
            stack = [(root, 0)]
            while stack:
                if cancel_event and cancel_event.is_set():
                    raise InterruptedError("Index scan cancelled; no partial filename scan was committed.")
                d, depth = stack.pop()
                try:
                    with os.scandir(d) as it:
                        entries = list(it)
                except OSError as exc:
                    root_complete = False
                    inaccessible_count += 1
                    if len(inaccessible) < 25:
                        inaccessible.append(f"{d}: {exc}")
                    continue

                is_project = bool({e.name.lower() for e in entries} & PROJECT_MARKERS)
                try:
                    mtime = os.stat(d).st_mtime
                except OSError:
                    mtime = 0.0
                batch.append((d, os.path.basename(d.rstrip("\\/")) or d, "",
                              1, int(is_project), 0, mtime, depth))
                counts["dirs"] += 1
                counts["projects"] += int(is_project)

                for e in entries:
                    try:
                        if _is_link(e):
                            continue
                        if e.is_dir(follow_symlinks=False):
                            if policy.skip_dir(e.name) or _hidden(e):
                                continue
                            stack.append((e.path, depth + 1))
                        elif e.is_file(follow_symlinks=False) and not _hidden(e):
                            ext = os.path.splitext(e.name)[1].lower()
                            if not policy.is_secret(e.name) and not e.name.lower().endswith(".devil-backup"):
                                st = e.stat()
                                batch.append((e.path, e.name, ext, 0, 0,
                                              st.st_size, st.st_mtime, depth + 1))
                                counts["files"] += 1
                    except OSError:
                        root_complete = False
                        inaccessible_count += 1
                        if len(inaccessible) < 25:
                            inaccessible.append(f"{e.path}: access failed")
                        continue

                if len(batch) >= 20000:
                    flush()

            if root_complete:
                flush()
                prefix = root.rstrip("\\/") + os.sep
                con.execute(
                    "DELETE FROM paths WHERE substr(path, 1, ?) = ? "
                    "AND path NOT IN (SELECT path FROM scan_seen)",
                    (len(prefix), prefix),
                )

        flush()
        con.execute("INSERT OR REPLACE INTO meta VALUES ('scanned_at', ?)",
                    (str(time.time()),))
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()

    counts["seconds"] = round(time.time() - started, 1)
    counts["roots"] = [str(r) for r in roots]
    counts["inaccessible"] = inaccessible
    counts["inaccessible_count"] = inaccessible_count
    return counts


_SQL = """
SELECT p.path, p.name, p.ext, p.is_dir, p.is_project, p.size, p.mtime,
       p.depth, bm25(paths_fts, 10.0, 1.0) AS score
FROM paths_fts JOIN paths p ON p.rowid = paths_fts.rowid
WHERE paths_fts MATCH ?{extra}
ORDER BY score LIMIT 200
"""
_KIND_SQL = {
    "any": "",
    "file": " AND p.is_dir = 0",
    "dir": " AND p.is_dir = 1",
    "project": " AND p.is_project = 1",
}
_TIER_WEIGHT = (1.0, 0.6, 0.3)  # name match > path match > any-term match


def _rank_factor(row, now: float) -> float:
    _, _, ext, is_dir, is_project, _, mtime, depth, _ = row
    f = 1.0 / (1.0 + 0.04 * depth)                     # shallower is better
    age_days = max(0.0, (now - mtime) / 86400)
    f *= 1.0 + 0.5 * math.exp(-age_days / 120)         # recently touched
    if ext in CODE_EXTENSIONS:
        f *= 1.3
    if is_project:
        f *= 1.4
    return f


def find(query: str, kind: str = "any", limit: int = 8) -> dict:
    terms = re.findall(r"\w+", query.lower())[:8]
    if not terms:
        return {"error": "Query cannot be empty."}
    if not is_built():
        return {"error": "The file-name index is not built yet. Ask the user "
                         "to run /scan in DEVIL, then try again."}

    extra = _KIND_SQL.get(kind, "")
    quoted = [f'"{t}"*' for t in terms]
    expressions = [
        f"name : ({' AND '.join(quoted)})",   # all terms in the file name
        " AND ".join(quoted),                  # all terms in name or path
        " OR ".join(quoted),                   # any term
    ]

    ranked, seen, now = [], set(), time.time()
    con = connect()
    try:
        for tier, expr in enumerate(expressions):
            for row in con.execute(_SQL.format(extra=extra), (expr,)):
                if row[0] in seen:
                    continue
                seen.add(row[0])
                ranked.append((row[8] * _rank_factor(row, now) * _TIER_WEIGHT[tier], row))
            if len(ranked) >= limit:
                break
    finally:
        con.close()

    ranked.sort(key=lambda t: t[0])
    results = []
    for _, row in ranked[:limit]:
        if not policy.is_allowed(row[0], is_dir=bool(row[3])):
            continue
        try:
            if not os.path.exists(row[0]):
                continue
        except OSError:
            continue
        results.append({
            "path": row[0],
            "kind": "project" if row[4] else ("dir" if row[3] else "file"),
            "modified": datetime.fromtimestamp(row[6]).strftime("%Y-%m-%d"),
            "size_kb": round(row[5] / 1024, 1) if not row[3] else None,
        })
    return {"results": results}
