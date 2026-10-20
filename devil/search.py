import re
from pathlib import Path

from . import indexer, policy
from .indexer import connect

# bm25() returns negative numbers; more negative = better match.
CODE_EXTENSIONS = {
    ".py", ".pyi", ".c", ".h", ".cpp", ".hpp", ".cc", ".rs", ".go",
    ".java", ".kt", ".cs", ".js", ".jsx", ".ts", ".tsx",
}
LOW_VALUE_EXTENSIONS = {
    ".md", ".txt", ".rst", ".json", ".jsonl", ".yaml", ".yml",
    ".xml", ".html", ".css",
}
MAX_TERMS = 8


def _run(con, expression: str, fetch: int) -> list[tuple]:
    return con.execute("""
        SELECT f.path, f.name, f.extension, f.size, f.modified,
               snippet(files_fts, 2, '[', ']', ' ... ', 24) AS excerpt,
               bm25(files_fts, 5.0, 8.0, 1.0) AS score
        FROM files_fts
        JOIN files f ON f.path = files_fts.path
        WHERE files_fts MATCH ?
        ORDER BY score
        LIMIT ?
    """, (expression, fetch)).fetchall()


def _adjusted(row: tuple) -> float:
    score, ext = row[6], row[2]
    if ext in CODE_EXTENSIONS:
        return score * 1.3   # more negative = ranks higher
    if ext in LOW_VALUE_EXTENSIONS:
        return score * 0.7
    return score


def search_files(query: str, limit: int = 10) -> list[dict]:
    """Search indexed filenames and contents with SQLite FTS5.

    Terms are prefix-matched ("detect" finds "detector"). All terms must
    match first; if that finds nothing, any term may match. Source-code
    files are ranked above docs and data files.
    """
    terms = re.findall(r"\w+", query)[:MAX_TERMS]
    if not terms:
        return []

    limit = max(1, min(limit, 50))
    fetch = min(limit * 3, 150)
    quoted = [f'"{t}"*' for t in terms]

    con = connect()
    try:
        match = "all_terms"
        rows = _run(con, " AND ".join(quoted), fetch)
        if not rows and len(terms) > 1:
            match = "any_term"
            rows = _run(con, " OR ".join(quoted), fetch)

        # FTS is a snapshot. Validate every candidate against current disk
        # metadata and access policy before exposing even a short excerpt.
        with indexer.DB_LOCK:
            refreshed = False
            invalid: list[str] = []
            for row in rows:
                path = Path(row[0])
                if not policy.is_allowed(path) or not indexer.should_index(path):
                    invalid.append(row[0])
                    continue
                try:
                    stat = path.stat()
                except OSError:
                    invalid.append(row[0])
                    continue
                if stat.st_size != row[3] or stat.st_mtime != row[4]:
                    if not indexer.index_file(con, path):
                        invalid.append(row[0])
                    refreshed = True

            for path in invalid:
                con.execute("DELETE FROM files_fts WHERE path = ?", (path,))
                con.execute("DELETE FROM files WHERE path = ?", (path,))
            if invalid or refreshed:
                con.commit()
                rows = _run(con, " AND ".join(quoted), fetch)
                if not rows and len(terms) > 1:
                    match = "any_term"
                    rows = _run(con, " OR ".join(quoted), fetch)
    finally:
        con.close()

    rows.sort(key=_adjusted)
    return [
        {
            "path": row[0],
            "name": row[1],
            "extension": row[2],
            "size": row[3],
            "excerpt": row[5],
            "score": row[6],
            "match": match,
        }
        for row in rows[:limit]
    ]
