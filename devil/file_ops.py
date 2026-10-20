"""Approved, bounded file modifications for DEVIL's local tool loop."""
from __future__ import annotations

import os
import hashlib
from pathlib import Path, PurePosixPath, PureWindowsPath
import stat
import tempfile
from datetime import datetime, timezone
import zipfile
import sqlite3
from contextlib import closing

from . import policy


MAX_TEXT_BYTES = 1_000_000
MAX_ARCHIVE_FILES = 20_000
MAX_ARCHIVE_BYTES = 1_073_741_824
MAX_EXTRACT_FILES = 10_000
MAX_EXTRACT_BYTES = 536_870_912
ACTION_DB = policy.ROOT / "data" / "actions.db"


def _record_action(action: str, target: Path, *, backup=None, trash=None, post_hash=None):
    ACTION_DB.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(ACTION_DB, timeout=10)) as con:
        with con:
            con.execute("""CREATE TABLE IF NOT EXISTS actions (
            id INTEGER PRIMARY KEY, action TEXT NOT NULL, target TEXT NOT NULL,
            backup TEXT, trash TEXT, post_hash TEXT, state TEXT NOT NULL DEFAULT 'active',
            created REAL NOT NULL DEFAULT (unixepoch())
        )""")
            con.execute("INSERT INTO actions(action,target,backup,trash,post_hash) VALUES(?,?,?,?,?)",
                        (action, str(target), str(backup) if backup else None,
                         str(trash) if trash else None, post_hash))


def _content_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _is_link_like(path: Path) -> bool:
    is_junction = getattr(path, "is_junction", None)
    return path.is_symlink() or bool(is_junction and is_junction())


def _terminal_safe(text: str) -> str:
    """Keep untrusted paths and previews from emitting terminal controls."""
    return "".join(
        char if char in "\n\t" or ord(char) >= 32 and ord(char) != 127
        else f"\\x{ord(char):02x}"
        for char in text
    )


def _safe_path(raw: str, *, directory: bool = False) -> tuple[Path | None, str | None]:
    if not raw.strip():
        return None, "A path is required."
    candidate = Path(raw).expanduser()
    try:
        # Reject symlinks anywhere in the path so an allowed-looking path
        # cannot redirect an operation to a different location.
        current = Path(os.path.abspath(candidate))
        while True:
            if _is_link_like(current):
                return None, "Symbolic links and junctions are not allowed for file operations."
            if current.parent == current:
                break
            current = current.parent

        resolved = candidate.resolve(strict=False)
        if not policy.is_allowed(resolved, is_dir=directory):
            return None, "Path is outside allowed folders or is a protected system, junk, or secret location."
        return resolved, None
    except (OSError, RuntimeError) as exc:
        return None, f"Could not resolve path: {exc}"


def _confirm(action: str) -> bool:
    print(f"\nApproval required: {_terminal_safe(action)}")
    try:
        answer = input("Type YES to approve; anything else cancels: ")
    except (EOFError, KeyboardInterrupt):
        print("Cancelled; no files were changed.")
        return False
    approved = answer.strip().casefold() == "yes"
    if not approved:
        print("Cancelled; no files were changed.")
    return approved


def _ready_file_target(raw: str) -> tuple[Path | None, bool, str | None]:
    target, error = _safe_path(raw)
    if error:
        return None, False, error
    assert target is not None
    if not target.parent.is_dir():
        return None, False, "The parent folder must already exist."
    if target.exists() and not target.is_file():
        return None, False, "The target exists and is not a file."
    return target, target.exists(), None


def _atomic_write(target: Path, data: bytes) -> None:
    fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.devil-", suffix=".tmp",
                                     dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, target)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def _file_signature(path: Path) -> tuple[int, int, int, int] | None:
    """Identify the file version approved by the user."""
    try:
        st = path.stat()
        return st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns
    except OSError:
        return None


def _backup_path(target: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    candidate = target.with_name(f"{target.name}.{stamp}.devil-backup")
    suffix = 1
    while candidate.exists():
        candidate = target.with_name(
            f"{target.name}.{stamp}.{suffix}.devil-backup"
        )
        suffix += 1
    return candidate


def _preview(text: str, limit: int = 240) -> str:
    compact = text.replace("\r", "\\r").replace("\n", "\\n")
    return compact[:limit] + ("…" if len(compact) > limit else "")


def write_file(args: dict) -> dict:
    raw_path = str(args.get("path", "")).strip()
    content = args.get("content")
    if not isinstance(content, str):
        return {"error": "File content must be text."}
    data = content.encode("utf-8")
    if len(data) > MAX_TEXT_BYTES:
        return {"error": f"Text files are limited to {MAX_TEXT_BYTES} bytes."}
    target, existed, error = _ready_file_target(raw_path)
    if error:
        return {"error": error}
    assert target is not None
    operation = "overwrite" if existed else "create"
    approved_signature = _file_signature(target) if existed else None
    original = target.read_bytes() if existed else None
    backup = _backup_path(target) if existed else None
    if len(content) <= 6000:
        content_details = f"\nComplete content follows:\n{content}"
    else:
        content_details = f"\nContent preview: {_preview(content)} (preview truncated)."
    if not _confirm(
        f"{operation} file {target} ({len(data)} bytes).{content_details}"
        + (f"\nA recovery copy will be saved to {backup}." if backup else "")
    ):
        return {"cancelled": True, "path": str(target)}
    try:
        # Recheck after approval in case the path changed while awaiting input.
        checked, now_exists, error = _ready_file_target(str(target))
        if error or checked != target or now_exists != existed:
            return {"error": error or "The target changed while waiting for approval."}
        if existed and _file_signature(target) != approved_signature:
            return {"error": "The target changed while waiting for approval; no write was made."}
        if existed and original is not None and backup:
            _atomic_write(backup, original)
        _atomic_write(target, data)
        if target.read_bytes() != data:
            return {"error": "Write verification failed after saving the file."}
    except OSError as exc:
        return {"error": f"Could not write file: {exc}"}
    _record_action(operation, target, backup=backup,
                   post_hash=hashlib.sha256(data).hexdigest())
    return {"path": str(target), "bytes_written": len(data), "action": operation,
            "backup": str(backup) if backup else None, "undo_available": True}


def edit_file(args: dict) -> dict:
    raw_path = str(args.get("path", "")).strip()
    old = args.get("old_text")
    new = args.get("new_text")
    if not isinstance(old, str) or not old:
        return {"error": "old_text must be a non-empty exact text match."}
    if not isinstance(new, str):
        return {"error": "new_text must be text."}
    target, existed, error = _ready_file_target(raw_path)
    if error:
        return {"error": error}
    assert target is not None
    if not existed:
        return {"error": "The file to edit does not exist."}
    try:
        original = target.read_bytes()
        if len(original) > MAX_TEXT_BYTES:
            return {"error": f"Text files are limited to {MAX_TEXT_BYTES} bytes."}
        text = original.decode("utf-8")
    except UnicodeDecodeError:
        return {"error": "Only UTF-8 text files can be edited."}
    except OSError as exc:
        return {"error": f"Could not read file: {exc}"}

    normalized_text = text.replace("\r\n", "\n").replace("\r", "\n")
    normalized_old = old.replace("\r\n", "\n").replace("\r", "\n")
    normalized_new = new.replace("\r\n", "\n").replace("\r", "\n")
    newline = "\r\n" if "\r\n" in text else ("\r" if "\r" in text else "\n")
    occurrences = normalized_text.count(normalized_old)
    replace_all = args.get("replace_all") is True
    if occurrences == 0:
        return {"error": "old_text was not found exactly in the file."}
    if occurrences != 1 and not replace_all:
        return {"error": f"old_text occurs {occurrences} times; set replace_all=true to replace all occurrences."}
    updated_normalized = (
        normalized_text.replace(normalized_old, normalized_new)
        if replace_all
        else normalized_text.replace(normalized_old, normalized_new, 1)
    )
    updated = updated_normalized.replace("\n", newline)
    data = updated.encode("utf-8")
    if len(data) > MAX_TEXT_BYTES:
        return {"error": f"Edited text would exceed {MAX_TEXT_BYTES} bytes."}
    if updated == text:
        return {"path": str(target), "action": "unchanged"}

    change_details = (
        f"\nOld text:\n{old}\nNew text:\n{new}"
        if len(old) + len(new) <= 6000
        else f"\nOld preview: {_preview(old)} | New preview: {_preview(new)} (previews truncated)."
    )
    backup = _backup_path(target)
    if not _confirm(
        f"edit {target}; replace {occurrences if replace_all else 1} occurrence(s)."
        f"{change_details}\nA recovery copy will be saved to {backup}."
    ):
        return {"cancelled": True, "path": str(target)}
    approved_content = hashlib.sha256(original).digest()
    checked, now_exists, error = _ready_file_target(str(target))
    if error or checked != target or not now_exists:
        return {"error": error or "The target changed while waiting for approval."}
    try:
        if hashlib.sha256(target.read_bytes()).digest() != approved_content:
            return {"error": "The file changed while waiting for approval; no edit was made."}
        _atomic_write(backup, original)
        _atomic_write(target, data)
        if target.read_bytes() != data:
            return {"error": "Edit verification failed after saving the file."}
    except OSError as exc:
        return {"error": f"Could not edit file: {exc}"}
    _record_action("edit", target, backup=backup,
                   post_hash=hashlib.sha256(data).hexdigest())
    return {"path": str(target), "backup": str(backup),
            "replacements": occurrences if replace_all else 1}


def delete_file(args: dict) -> dict:
    raw_path = str(args.get("path", "")).strip()
    target, existed, error = _ready_file_target(raw_path)
    if error:
        return {"error": error}
    assert target is not None
    if not existed:
        return {"error": "The file does not exist."}
    approved_signature = _file_signature(target)
    recovery_dir = target.parent / ".devil_recovery"
    recovery_target = recovery_dir / f"{target.name}.{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}.deleted"
    if not _confirm(f"move file {target} into recoverable DEVIL quarantine at {recovery_target}"):
        return {"cancelled": True, "path": str(target)}
    checked, now_exists, error = _ready_file_target(str(target))
    if error or checked != target or not now_exists:
        return {"error": error or "The target changed while waiting for approval."}
    if _file_signature(target) != approved_signature:
        return {"error": "The file changed while waiting for approval; no deletion was made."}
    try:
        if _file_signature(target) != approved_signature:
            return {"error": "The file changed while waiting for approval; no deletion was made."}
        recovery_dir.mkdir(exist_ok=True)
        os.replace(target, recovery_target)
    except OSError as exc:
        return {"error": f"Could not delete file: {exc}"}
    _record_action("delete", target, trash=recovery_target,
                   post_hash=_content_hash(recovery_target))
    return {"path": str(target), "deleted": True, "recoverable": True,
            "recovery_path": str(recovery_target), "undo_available": True}


def undo_last(args: dict | None = None) -> dict:
    """Restore the most recent active create/write/edit/delete operation."""
    with closing(sqlite3.connect(ACTION_DB, timeout=10)) as con:
        con.row_factory = sqlite3.Row
        with con:
            con.execute("""CREATE TABLE IF NOT EXISTS actions (
            id INTEGER PRIMARY KEY, action TEXT NOT NULL, target TEXT NOT NULL,
            backup TEXT, trash TEXT, post_hash TEXT, state TEXT NOT NULL DEFAULT 'active',
            created REAL NOT NULL DEFAULT (unixepoch())
        )""")
            row = con.execute("SELECT * FROM actions WHERE state='active' ORDER BY id DESC LIMIT 1").fetchone()
    if not row:
        return {"error": "There is no recorded file operation to undo."}
    target = Path(row["target"])
    checked, error = _safe_path(str(target))
    if error or checked != target:
        return {"error": error or "The original path is no longer allowed."}
    backup = Path(row["backup"]) if row["backup"] else None
    trash = Path(row["trash"]) if row["trash"] else None
    action = row["action"]
    if action == "delete":
        if not trash or not trash.is_file() or target.exists():
            return {"error": "Cannot safely restore: recovery copy is missing or the original path is occupied."}
        if trash.parent != target.parent / ".devil_recovery" or _is_link_like(trash.parent) or _is_link_like(trash):
            return {"error": "Recovery path failed validation; refusing to restore it."}
        if _content_hash(trash) != row["post_hash"]:
            return {"error": "Recovery copy changed; refusing to restore it."}
        if not _confirm(f"restore {target} from recovery copy {trash}"):
            return {"cancelled": True}
        if target.exists() or _is_link_like(trash.parent) or _is_link_like(trash):
            return {"error": "The target or recovery path changed while waiting for approval."}
        os.replace(trash, target)
    elif action in {"edit", "overwrite"}:
        if not backup or not backup.is_file() or not target.is_file():
            return {"error": "Cannot safely undo: target or recovery copy is missing."}
        checked_backup, backup_error = _safe_path(str(backup))
        if backup_error or checked_backup != backup or _is_link_like(backup):
            return {"error": backup_error or "Recovery copy failed path validation."}
        if _content_hash(target) != row["post_hash"]:
            return {"error": "The file changed after DEVIL's operation; refusing to overwrite newer content."}
        original = backup.read_bytes()
        if not _confirm(f"restore {target} from recovery copy {backup}"):
            return {"cancelled": True}
        if _content_hash(target) != row["post_hash"]:
            return {"error": "The target changed while waiting for approval."}
        _atomic_write(target, original)
    elif action == "create":
        if not target.is_file() or _content_hash(target) != row["post_hash"]:
            return {"error": "Cannot safely undo: created file is missing or has changed."}
        if not _confirm(f"move DEVIL-created file {target} into recoverable quarantine"):
            return {"cancelled": True}
        recovery_dir = target.parent / ".devil_recovery"
        recovery_dir.mkdir(exist_ok=True)
        trash = recovery_dir / f"{target.name}.{row['id']}.created"
        if target.exists() and _content_hash(target) == row["post_hash"]:
            os.replace(target, trash)
        else:
            return {"error": "The target changed while waiting for approval."}
    else:
        return {"error": f"Undo is not supported for {action}."}
    with closing(sqlite3.connect(ACTION_DB, timeout=10)) as con:
        with con:
            con.execute("UPDATE actions SET state='undone' WHERE id=?", (row["id"],))
    return {"undone": action, "path": str(target), "recovery_copy": str(backup or trash) if (backup or trash) else None}


def _folder_files(root: Path, excluded: Path) -> tuple[list[Path], str | None]:
    files: list[Path] = []
    total_bytes = 0
    walk_errors: list[str] = []

    def on_error(exc: OSError) -> None:
        walk_errors.append(str(exc))

    for current, dirs, names in os.walk(root, topdown=True, followlinks=False,
                                         onerror=on_error):
        current_path = Path(current)
        dirs[:] = [name for name in dirs
                   if not policy.skip_dir(name)
                   and not _is_link_like(current_path / name)
                   and policy.is_allowed(current_path / name, is_dir=True)]
        for name in names:
            path = current_path / name
            if path == excluded or _is_link_like(path) or not policy.is_allowed(path):
                continue
            try:
                size = path.stat().st_size
            except OSError as exc:
                return [], f"Could not inspect {path}: {exc}"
            total_bytes += size
            if total_bytes > MAX_ARCHIVE_BYTES:
                return [], f"Folder exceeds the {MAX_ARCHIVE_BYTES}-byte archive limit."
            files.append(path)
            if len(files) > MAX_ARCHIVE_FILES:
                return [], f"Folder exceeds the {MAX_ARCHIVE_FILES}-file archive limit."
    if walk_errors:
        return [], f"Could not fully scan folder: {walk_errors[0]}"
    return files, None


def zip_folder(args: dict) -> dict:
    raw_source = str(args.get("folder", "")).strip()
    raw_archive = str(args.get("destination", "")).strip()
    source, error = _safe_path(raw_source, directory=True)
    if error:
        return {"error": error}
    assert source is not None
    if not source.is_dir():
        return {"error": "folder must be an existing directory."}
    target, existed, error = _ready_file_target(raw_archive)
    if error:
        return {"error": error}
    assert target is not None
    if target.suffix.lower() != ".zip":
        return {"error": "destination must end in .zip."}
    files, error = _folder_files(source, target)
    if error:
        return {"error": error}
    total = sum(path.stat().st_size for path in files)
    mode = "overwrite" if existed else "create"
    if not _confirm(
        f"{mode} ZIP {target} from {source}; include {len(files)} allowed files "
        f"({total} bytes). Protected, system, junk, and linked paths are excluded."
    ):
        return {"cancelled": True, "destination": str(target)}

    checked, now_exists, error = _ready_file_target(str(target))
    if error or checked != target or now_exists != existed:
        return {"error": error or "The archive target changed while waiting for approval."}
    fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.devil-", suffix=".tmp",
                                     dir=target.parent)
    os.close(fd)
    try:
        with zipfile.ZipFile(temp_name, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in files:
                arcname = Path(source.name) / path.relative_to(source)
                archive.write(path, arcname.as_posix())
        os.replace(temp_name, target)
    except Exception as exc:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        return {"error": f"Could not create ZIP: {type(exc).__name__}: {exc}"}
    return {"archive": str(target), "files_added": len(files), "source_bytes": total}


def unzip_files(args: dict) -> dict:
    raw_archive = str(args.get("archive", "")).strip()
    raw_destination = str(args.get("destination", "")).strip()
    archive_path, error = _safe_path(raw_archive)
    if error:
        return {"error": error}
    assert archive_path is not None
    if not archive_path.is_file() or not zipfile.is_zipfile(archive_path):
        return {"error": "archive must be an existing ZIP file."}

    destination, error = _safe_path(raw_destination, directory=True)
    if error:
        return {"error": error}
    assert destination is not None
    if destination.exists() and not destination.is_dir():
        return {"error": "destination exists and is not a folder."}
    if not destination.parent.is_dir():
        return {"error": "The destination's parent folder must already exist."}

    try:
        archive = zipfile.ZipFile(archive_path, "r")
    except (OSError, zipfile.BadZipFile) as exc:
        return {"error": f"Could not open ZIP: {exc}"}

    with archive:
        infos = archive.infolist()
        if len(infos) > MAX_EXTRACT_FILES:
            return {"error": f"ZIP exceeds the {MAX_EXTRACT_FILES}-entry limit."}
        accepted: list[tuple[zipfile.ZipInfo, tuple[str, ...], Path]] = []
        seen: set[str] = set()
        total_size = 0
        overwrite_count = 0
        for info in infos:
            raw_name = info.filename.replace("\\", "/")
            posix = PurePosixPath(raw_name)
            windows = PureWindowsPath(info.filename)
            parts = tuple(part for part in posix.parts if part not in ("", "."))
            if (not parts or posix.is_absolute() or windows.is_absolute()
                    or windows.drive or any(part == ".." for part in parts)):
                return {"error": f"Unsafe ZIP path rejected: {info.filename!r}."}
            mode = (info.external_attr >> 16) & 0xFFFF
            if stat.S_ISLNK(mode):
                return {"error": f"ZIP contains a symbolic link: {info.filename!r}."}
            is_dir = info.is_dir()
            path = destination.joinpath(*parts)
            try:
                resolved = path.resolve(strict=False)
                resolved.relative_to(destination)
            except (OSError, RuntimeError, ValueError):
                return {"error": f"ZIP path escapes destination: {info.filename!r}."}
            checked_path, path_error = _safe_path(str(path), directory=is_dir)
            if path_error or checked_path != resolved:
                return {"error": path_error or f"ZIP target changed during validation: {path}."}
            if not policy.is_allowed(resolved, is_dir=is_dir):
                return {"error": f"ZIP contains a protected or disallowed path: {info.filename!r}."}
            key = str(resolved).casefold()
            if key in seen:
                return {"error": f"ZIP contains duplicate target paths: {info.filename!r}."}
            seen.add(key)
            if not is_dir:
                total_size += info.file_size
                if total_size > MAX_EXTRACT_BYTES:
                    return {"error": f"ZIP exceeds the {MAX_EXTRACT_BYTES}-byte extraction limit."}
                if path.exists():
                    if _is_link_like(path) or not path.is_file():
                        return {"error": f"Cannot overwrite a non-file target: {path}."}
                    overwrite_count += 1
            elif path.exists() and (_is_link_like(path) or not path.is_dir()):
                return {"error": f"Cannot create a directory over a non-directory: {path}."}
            for parent in path.parents:
                if parent == destination:
                    break
                if parent.exists() and (_is_link_like(parent) or not parent.is_dir()):
                    return {"error": f"Cannot extract through a linked or non-directory path: {parent}."}
            accepted.append((info, parts, path))

        # Reject file/directory name conflicts inside the archive before any writes.
        kinds: dict[str, bool] = {}
        for info, parts, _ in accepted:
            key = "/".join(parts).casefold()
            parent_key = key.rpartition("/")[0]
            while parent_key:
                if kinds.get(parent_key) is False:
                    return {"error": f"ZIP has a file used as a parent directory: {info.filename!r}."}
                parent_key = parent_key.rpartition("/")[0]
            if info.is_dir() is False and any(
                existing.startswith(key + "/") for existing in kinds
            ):
                return {"error": f"ZIP has a file/directory name conflict: {info.filename!r}."}
            kinds[key] = info.is_dir()

        file_count = sum(not info.is_dir() for info, _, _ in accepted)
        if file_count == 0:
            return {"error": "ZIP contains no files to extract."}
        destination_exists = destination.exists()
        action = "into existing folder" if destination_exists else "create folder and extract into"
        overwrite_note = f"; overwrite {overwrite_count} existing file(s)" if overwrite_count else ""
        sample_paths = ", ".join(str(path) for _, _, path in accepted[:5])
        if len(accepted) > 5:
            sample_paths += f", and {len(accepted) - 5} more"
        if not _confirm(
            f"extract {file_count} files ({total_size} uncompressed bytes) from "
            f"{archive_path} {action} {destination}{overwrite_note}. "
            f"First targets: {sample_paths}."
        ):
            return {"cancelled": True, "destination": str(destination)}

        # Revalidate paths after the approval prompt before staging extraction.
        checked, error = _safe_path(str(destination), directory=True)
        checked_archive, archive_error = _safe_path(str(archive_path))
        if error or archive_error or checked != destination or checked_archive != archive_path:
            return {"error": error or archive_error or "Paths changed while waiting for approval."}

        try:
            with tempfile.TemporaryDirectory(prefix=".devil-unzip-",
                                             dir=destination.parent) as staging_name:
                staging = Path(staging_name)
                extracted = []
                copied_bytes = 0
                for info, parts, target in accepted:
                    if info.is_dir():
                        continue
                    staged = staging.joinpath(*parts)
                    staged.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(info, "r") as source_stream, staged.open("wb") as out:
                        while True:
                            chunk = source_stream.read(1024 * 1024)
                            if not chunk:
                                break
                            copied_bytes += len(chunk)
                            if copied_bytes > MAX_EXTRACT_BYTES:
                                raise ValueError("ZIP exceeded the extraction byte limit while reading.")
                            out.write(chunk)
                    extracted.append((staged, target))

                destination.mkdir(exist_ok=True)
                for info, parts, path in accepted:
                    if info.is_dir():
                        path.mkdir(parents=True, exist_ok=True)
                for staged, target in extracted:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(staged, target)
        except Exception as exc:
            return {"error": f"Extraction failed: {type(exc).__name__}: {exc}"}

    return {"destination": str(destination), "files_extracted": file_count,
            "bytes_extracted": total_size, "files_overwritten": overwrite_count}


def run_file_tool(name: str, args: dict) -> dict:
    handlers = {
        "write_file": write_file,
        "edit_file": edit_file,
        "delete_file": delete_file,
        "undo_last": undo_last,
        "zip_folder": zip_folder,
        "unzip_files": unzip_files,
    }
    handler = handlers.get(name)
    if handler is None:
        return {"error": f"Unsupported file operation: {name}."}
    try:
        return handler(args)
    except Exception as exc:
        return {"error": f"{name} failed: {type(exc).__name__}: {exc}"}
