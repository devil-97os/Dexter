"""Read-only measurements for DEVIL's current indexes plus isolated indexing trials."""
from __future__ import annotations

import statistics
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from devil import indexer, pathindex, search, policy


def timed(fn, repeats=7):
    values = []
    for _ in range(repeats):
        start = time.perf_counter()
        fn()
        values.append((time.perf_counter() - start) * 1000)
    return round(statistics.median(values), 2), round(max(values), 2)


def main():
    print("DEVIL retrieval benchmark (production index reads are read-only)")
    if pathindex.DB_PATH.exists():
        con = pathindex.connect()
        try:
            count = con.execute("SELECT COUNT(*) FROM paths WHERE is_dir=0").fetchone()[0]
        finally:
            con.close()
        print(f"filename index: {count:,} files; {pathindex.DB_PATH.stat().st_size:,} bytes")
        print("filename query median/max ms:", timed(lambda: pathindex.find("mw2", kind="file", limit=10)))
    else:
        print("filename index: not built")

    if indexer.DB_PATH.exists():
        con = indexer.connect()
        try:
            count = con.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        finally:
            con.close()
        print(f"content index: {count:,} files; {indexer.DB_PATH.stat().st_size:,} bytes")
        tracemalloc.start()
        print("content query median/max ms:", timed(lambda: search.search_files("obstacle detection", limit=5)))
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        print(f"content-search traced Python peak: {peak:,} bytes")
    else:
        print("content index: not built")

    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        for i in range(1000):
            (root / f"module_{i:04}.py").write_text(
                f"def routine_{i}():\n    return 'selective retrieval benchmark token {i}'\n",
                encoding="utf-8")
        with patch.object(indexer, "DB_PATH", root / "content.db"), \
             patch.object(pathindex, "DB_PATH", root / "paths.db"), \
             patch.object(policy, "load_roots", return_value=[root]):
            start = time.perf_counter()
            name_initial = pathindex.scan()
            name_initial_ms = (time.perf_counter() - start) * 1000
            start = time.perf_counter()
            name_unchanged = pathindex.scan()
            name_unchanged_ms = (time.perf_counter() - start) * 1000
            tracemalloc.start()
            start = time.perf_counter()
            first = indexer.index_folder(root)
            initial_ms = (time.perf_counter() - start) * 1000
            _, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            start = time.perf_counter()
            second = indexer.index_folder(root)
            unchanged_ms = (time.perf_counter() - start) * 1000
            start = time.perf_counter()
            (root / "module_0500.py").write_text("def routine_500():\n    return 'incremental delta token'\n", encoding="utf-8")
            incremental = indexer.index_folder(root)
            incremental_ms = (time.perf_counter() - start) * 1000
            print("synthetic 1,000-file content trial:", {
                "initial_ms": round(initial_ms, 2), "initial": first,
                "unchanged_ms": round(unchanged_ms, 2), "unchanged": second,
                "one_file_change_refresh_ms": round(incremental_ms, 2),
                "incremental": incremental, "peak_traced_python_bytes": peak,
                "db_bytes": (root / "content.db").stat().st_size,
            })
            print("synthetic 1,000-file filename trial:", {
                "initial_ms": round(name_initial_ms, 2), "initial": name_initial,
                "unchanged_ms": round(name_unchanged_ms, 2),
                "unchanged": name_unchanged,
                "db_bytes": (root / "paths.db").stat().st_size,
            })


if __name__ == "__main__":
    main()
