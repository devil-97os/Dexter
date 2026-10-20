"""Compact code maps so the model can see WHERE things live without guessing
keywords: a file's classes/functions with line numbers, or a folder's files
with their top-level symbols."""
from __future__ import annotations

import ast
import os
import re
from pathlib import Path

from . import policy
from .indexer import TEXT_EXTENSIONS
from .search import CODE_EXTENSIONS

MAX_FILE_BYTES = 500_000
_GENERIC = re.compile(
    r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?"
    r"(function\*?|class|def|fn|func|struct|enum|interface|trait|impl)\s+([A-Za-z_]\w*)"
)


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8-sig", errors="replace")


def file_symbols(path: Path) -> tuple[list[tuple[int, str]], int]:
    """Return ([(line, label)], total_lines). Methods are indented labels."""
    text = _read(path)
    total = text.count("\n") + 1

    if path.suffix.lower() in (".py", ".pyi"):
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError):
            tree = None
        if tree is not None:
            out: list[tuple[int, str]] = []
            funcs = (ast.FunctionDef, ast.AsyncFunctionDef)
            for node in tree.body:
                if isinstance(node, ast.ClassDef):
                    out.append((node.lineno, f"class {node.name}"))
                    for sub in node.body:
                        if isinstance(sub, funcs):
                            out.append((sub.lineno, f"  def {sub.name}"))
                elif isinstance(node, funcs):
                    out.append((node.lineno, f"def {node.name}"))
            return out, total

    out = []
    for i, line in enumerate(text.splitlines(), 1):
        m = _GENERIC.match(line)
        if m:
            out.append((i, f"{m.group(1)} {m.group(2)}"))
    return out, total


def _compact(symbols: list[tuple[int, str]], budget: int = 250) -> str:
    """Classes (with methods) first, then functions, within a char budget."""
    classes: list[list] = []
    funcs: list[str] = []
    cls = None
    for _, label in symbols:
        if label.startswith("  "):
            if cls is not None:
                cls[1].append(label.split()[-1])
        else:
            kind, name = label.split(" ", 1)
            if kind == "class":
                cls = [name, []]
                classes.append(cls)
            else:
                cls = None
                funcs.append(name)

    parts = [f"{n}[{','.join(m[:12])}]" for n, m in classes] + funcs
    out, used = [], 0
    for part in parts:
        if used + len(part) + 2 > budget:
            out.append("...")
            break
        out.append(part)
        used += len(part) + 2
    return ", ".join(out)


def folder_map(root: Path, max_files: int = 40, max_chars: int = 3100) -> dict:
    """Files under root (policy-filtered) with their top-level symbols."""
    files: list[Path] = []
    for cur, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = sorted(
            d for d in dirs
            if not policy.skip_dir(d) and not os.path.islink(os.path.join(cur, d))
        )
        for n in sorted(names):
            p = Path(cur) / n
            if (p.suffix.lower() in TEXT_EXTENSIONS and not policy.is_secret(n)
                    and not p.is_symlink()):
                files.append(p)
        if len(files) > 400:
            break

    # Shallow files first; code before docs/config.
    files.sort(key=lambda p: (len(p.relative_to(root).parts),
                              p.suffix.lower() not in CODE_EXTENSIONS, str(p)))

    lines, used = [], 0
    for p in files[:max_files]:
        rel = p.relative_to(root).as_posix()
        try:
            if p.suffix.lower() in CODE_EXTENSIONS and p.stat().st_size <= MAX_FILE_BYTES:
                symbols, total = file_symbols(p)
                line = f"{rel} ({total} lines): {_compact(symbols)}"[:300]
            else:
                line = rel
        except OSError:
            continue
        if used + len(line) + 1 > max_chars:
            break
        lines.append(line)
        used += len(line) + 1

    return {
        "path": str(root),
        "files_shown": len(lines),
        "files_total": len(files),
        "content": "\n".join(lines) if lines else "(no readable files)",
        "truncated": len(lines) < len(files),
    }