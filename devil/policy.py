"""One place that decides what DEVIL may look at.

Used by the file-name index, the content indexer and the read tools, so the
rules can't drift apart. Nothing outside the allowed roots is ever listed,
indexed or read, and OS / junk / secret locations inside them are skipped.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "data" / "roots.json"

# Windows / OS locations: never entered, even if a root happens to contain them.
OS_DIRS = {
    "windows", "program files", "program files (x86)", "programdata",
    "$recycle.bin", "system volume information", "recovery", "appdata",
    "perflogs", "msocache", "documents and settings", "config.msi",
    "windowsapps", "intel", "amd", "nvidia", "drivers",
}
# Generated / vendored directories: huge and never what the user means.
JUNK_DIRS = {
    "node_modules", "__pycache__", "venv", "site-packages", "dist", "build",
    "target", "bin", "obj", "cache", "__macosx", "bower_components",
    ".git", ".hg", ".svn", ".venv", ".tox", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", ".next", ".nuxt", ".turbo", ".gradle",
    ".devil_recovery",
}
SKIP_DIRS = OS_DIRS | JUNK_DIRS


def skip_dir(name: str) -> bool:
    n = name.lower()
    return n.startswith("$") or n in SKIP_DIRS or n.endswith(".egg-info")


_SECRET_EXACT = {
    ".npmrc", ".pypirc", ".netrc", "secrets.json", "credentials.json",
    "credentials", "id_rsa", "id_ed25519", "id_ecdsa", "service-account.json",
}
_SECRET_SUFFIX = (".pem", ".key", ".pfx", ".p12", ".kdbx", ".jks",
                  ".keystore", ".ppk")
_ENV_SAFE_SUFFIX = (".example", ".sample", ".template")
_CONFIGY = {".json", ".txt", ".yaml", ".yml", ".ini", ".cfg", ".conf",
            ".toml", ""}
_SECRET_WORDS = ("secret", "credential", "password", "passwd", "apikey",
                 "api_key", "api-key", "private_key")
_TOKEN_RE = re.compile(r"^(token|tokens|auth|oauth)[^/\\]*\.(json|txt|ya?ml|ini|cfg)$")


def is_secret(name: str) -> bool:
    n = name.lower()
    if n.startswith(".env"):
        return not n.endswith(_ENV_SAFE_SUFFIX)
    if n in _SECRET_EXACT or n.endswith(_SECRET_SUFFIX):
        return True
    if os.path.splitext(n)[1] in _CONFIGY and any(w in n for w in _SECRET_WORDS):
        return True
    return bool(_TOKEN_RE.match(n))


def _defaults() -> list[Path]:
    home = Path.home()
    candidates = [ROOT.parent, home / "Documents", home / "Desktop",
                  home / "Downloads"]
    return [c for c in candidates if c.is_dir()]


def _local_drive_roots() -> list[Path]:
    """Return mounted Windows fixed drives, excluding network/removable media."""
    if os.name != "nt":
        return _defaults()

    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetLogicalDrives.restype = ctypes.c_uint
    kernel32.GetDriveTypeW.argtypes = [ctypes.c_wchar_p]
    kernel32.GetDriveTypeW.restype = ctypes.c_uint
    drive_mask = kernel32.GetLogicalDrives()
    roots = []
    for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        if not drive_mask & (1 << (ord(letter) - ord("A"))):
            continue
        root = f"{letter}:\\"
        if kernel32.GetDriveTypeW(root) == 3:  # DRIVE_FIXED
            roots.append(Path(root))
    return roots


def _dedupe(paths: list[Path]) -> list[Path]:
    kept: list[Path] = []
    for p in sorted({p.resolve() for p in paths}, key=lambda x: len(x.parts)):
        if not any(r == p or r in p.parents for r in kept):
            kept.append(p)
    return kept


def load_roots() -> list[Path]:
    """Allowed roots: DEVIL_ROOTS env, configured roots, or local drives."""
    env = os.getenv("DEVIL_ROOTS")
    raw: list[Path] | None = None

    if env:
        raw = [Path(x).expanduser() for x in env.split(os.pathsep) if x.strip()]
    else:
        if CONFIG_PATH.exists():
            try:
                data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
                if data.get("all_local_drives"):
                    raw = _local_drive_roots()
                else:
                    raw = [Path(x).expanduser() for x in data.get("roots", [])]
            except (OSError, ValueError, AttributeError):
                raw = None
        if raw is None:
            raw = _defaults()
            try:
                CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
                CONFIG_PATH.write_text(
                    json.dumps({"roots": [str(p) for p in raw]}, indent=2),
                    encoding="utf-8",
                )
            except OSError:
                pass

    existing = []
    for p in raw:
        try:
            if p.is_dir():
                existing.append(p)
        except OSError:
            pass
    return _dedupe(existing)


def is_allowed(path, is_dir: bool = False) -> bool:
    """True if path is inside an allowed root and not OS/junk/secret."""
    try:
        p = Path(path).resolve()
    except (OSError, RuntimeError):
        return False
    if is_secret(p.name):
        return False

    for root in load_roots():
        if p == root or root in p.parents:
            rel = p.relative_to(root).parts
            dirs = rel if is_dir else rel[:-1]
            return not any(skip_dir(part) for part in dirs)
    return False
