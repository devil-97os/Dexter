"""Bounded background jobs for long-running local indexing operations."""
from __future__ import annotations

import threading
import time
import uuid
import sqlite3
from contextlib import closing
from pathlib import Path


class IndexJobManager:
    def __init__(self):
        self._lock = threading.RLock()
        self._job = None

    def start(self, label, target):
        with self._lock:
            if self._job and self._job["status"] in {"queued", "running"}:
                return {"error": "An indexing job is already active.", "job": self.snapshot()}
            job = {"id": uuid.uuid4().hex[:8], "label": label, "status": "queued",
                   "started": time.time(), "progress": {}, "result": None,
                   "error": None, "cancel": threading.Event()}
            self._job = job
            thread = threading.Thread(target=self._run, args=(job, target),
                                      name="devil-index", daemon=True)
            job["thread"] = thread
            thread.start()
            return self._public(job)

    def _run(self, job, target):
        with self._lock:
            job["status"] = "running"
        def progress(value):
            with self._lock:
                job["progress"] = dict(value)
        try:
            result = target(job["cancel"], progress)
            with self._lock:
                job["result"] = result
                job["status"] = "cancelled" if job["cancel"].is_set() else "completed"
        except Exception as exc:
            with self._lock:
                job["error"] = f"{type(exc).__name__}: {exc}"
                job["status"] = "cancelled" if job["cancel"].is_set() else "failed"

    def cancel(self):
        with self._lock:
            if not self._job or self._job["status"] not in {"queued", "running"}:
                return {"error": "No active indexing job."}
            self._job["cancel"].set()
            return {"cancel_requested": True, "job": self._public(self._job)}

    def snapshot(self):
        with self._lock:
            return self._public(self._job) if self._job else None

    @staticmethod
    def _public(job):
        if not job:
            return None
        return {k: job[k] for k in ("id", "label", "status", "progress", "result", "error")}


def index_projects(cancel_event, on_progress, *, max_files=30_000,
                   max_files_per_project=3_000):
    """Index recognized projects from the persistent filename index, within a global cap."""
    from . import pathindex, policy
    from . import indexer
    from .indexer import index_folder
    con = pathindex.connect()
    try:
        candidates = [Path(row[0]) for row in con.execute(
            "SELECT path FROM paths WHERE is_dir=1 AND is_project=1 ORDER BY depth, path"
        ) if policy.is_allowed(row[0], is_dir=True)]
    finally:
        con.close()
    home = Path.home()
    for folder in (home / "Documents", home / "Desktop", home / "Downloads"):
        if folder.is_dir() and policy.is_allowed(folder, is_dir=True):
            candidates.append(folder)
    candidates.sort(key=lambda item: (len(item.parts), str(item).casefold()))
    projects = []
    for candidate in candidates:
        if not any(candidate == root or root in candidate.parents for root in projects):
            projects.append(candidate)
    meta_db = indexer.DB_PATH
    meta_db.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(meta_db, timeout=30)) as con:
        con.execute("CREATE TABLE IF NOT EXISTS index_jobs_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        cursor = con.execute("SELECT value FROM index_jobs_meta WHERE key='projects_cursor'").fetchone()
        last_project = cursor[0] if cursor else ""
    if last_project:
        after = [p for p in projects if str(p).casefold() > last_project.casefold()]
        before = [p for p in projects if str(p).casefold() <= last_project.casefold()]
        projects = after + before
    totals = {"projects": 0, "scanned": 0, "indexed": 0, "skipped": 0, "capped": False}
    cursor_value = last_project
    for folder_path in projects:
        folder = str(folder_path)
        if cancel_event.is_set() or totals["scanned"] >= max_files:
            totals["capped"] = totals["scanned"] >= max_files
            break
        result = index_folder(folder, max_files=min(
                                  max_files - totals["scanned"], max_files_per_project),
                              cancel_event=cancel_event,
                              on_progress=lambda p: on_progress({**totals, **p,
                                                                 "project": folder,
                                                                 "projects_total": len(projects)}))
        totals["projects"] += 1
        cursor_value = folder
        for key in ("scanned", "indexed", "skipped"):
            totals[key] += result.get(key, 0)
        if result.get("capped"):
            totals["projects_capped"] = totals.get("projects_capped", 0) + 1
        on_progress({**totals, "project": folder, "projects_total": len(projects)})
        with closing(sqlite3.connect(meta_db, timeout=30)) as con:
            with con:
                con.execute("INSERT OR REPLACE INTO index_jobs_meta(key,value) VALUES('projects_cursor',?)",
                            (cursor_value,))
        if totals["scanned"] >= max_files:
            totals["capped"] = True
            break
    return totals


manager = IndexJobManager()
