"""Loopback HTTP bridge between the DEVIL Python systems and the React UI."""
from __future__ import annotations

import base64
import copy
import json
import ipaddress
import mimetypes
import os
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from devil import file_ops, indexer, pathindex, policy, search
from devil.agent import (GEMINI_MODEL, GROQ_MODEL, OPENROUTER_MODEL,
                         DevilAgent, run_local_tool)
from devil.jobs import manager as index_jobs, index_projects
from devil.voice import speak_reply, transcribe_audio

ROOT = Path(__file__).resolve().parent
UI_DIST = ROOT / "ui" / "dist"
DB_PATH = ROOT / "data" / "dexter.db"
HOST = os.getenv("DEVIL_UI_HOST", "127.0.0.1")
PORT = int(os.getenv("DEVIL_UI_PORT", "8765"))
MAX_JSON_BYTES = 14_000_000
ALLOWED_HOSTS = {f"127.0.0.1:{PORT}", f"localhost:{PORT}",
                 "127.0.0.1:5173", "localhost:5173"}
ALLOWED_ORIGINS = {f"http://{host}" for host in ALLOWED_HOSTS}
CHAT_LOCK = threading.RLock()
CONFIRM_LOCK = threading.RLock()
PENDING_CONFIRMATIONS: dict[str, dict] = {}
AGENT = DevilAgent()

SECRET_VALUE_RE = re.compile(
    r"(?i)(?:((api[_-]?key|password|passwd|secret|access[_-]?token|refresh[_-]?token)"
    r"\s*[:=]\s*['\"]?[^\s'\";,]{8,})|"
    r"\b(?:sk-[A-Za-z0-9_-]{20,}|gsk_[A-Za-z0-9_-]{20,}|"
    r"gh[pousr]_[A-Za-z0-9]{30,}|AIza[A-Za-z0-9_-]{30,})|"
    r"\bBearer\s+[A-Za-z0-9._-]{24,})"
)


@contextmanager
def database():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=20)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


def initialize_database():
    with database() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS conversations(
            id TEXT PRIMARY KEY, title TEXT NOT NULL, created TEXT NOT NULL, updated TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS messages(
            id INTEGER PRIMARY KEY, conversation_id TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('user','assistant')),
            content TEXT NOT NULL, provider TEXT, created TEXT NOT NULL,
            excluded_training INTEGER NOT NULL DEFAULT 1,
            FOREIGN KEY(conversation_id) REFERENCES conversations(id)
        );
        CREATE TABLE IF NOT EXISTS memories(
            id INTEGER PRIMARY KEY, content TEXT NOT NULL, category TEXT NOT NULL,
            source_type TEXT NOT NULL CHECK(source_type IN ('confirmed','inference')),
            confidence REAL NOT NULL DEFAULT 1.0,
            created TEXT NOT NULL, updated TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS training_examples(
            id INTEGER PRIMARY KEY, message_id INTEGER, prompt TEXT NOT NULL,
            response TEXT NOT NULL, teacher TEXT NOT NULL, category TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK(status IN ('pending','accepted','rejected')),
            created TEXT NOT NULL, updated TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS events(
            id INTEGER PRIMARY KEY, type TEXT NOT NULL, status TEXT NOT NULL,
            provider TEXT, detail TEXT NOT NULL, created TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS settings(
            key TEXT PRIMARY KEY, value TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS messages_conversation_idx
            ON messages(conversation_id, id);
        CREATE INDEX IF NOT EXISTS events_created_idx ON events(created DESC);
        """)
        row = con.execute("SELECT value FROM settings WHERE key='active_conversation'").fetchone()
        if row:
            exists = con.execute("SELECT 1 FROM conversations WHERE id=?", (row[0],)).fetchone()
        else:
            exists = None
        if not exists:
            cid = uuid.uuid4().hex
            now = utc_now()
            con.execute("INSERT INTO conversations VALUES(?,?,?,?)",
                        (cid, "New conversation", now, now))
            con.execute("INSERT OR REPLACE INTO settings VALUES('active_conversation',?)", (cid,))


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def event(event_type: str, status: str, detail: str, provider: str | None = None):
    with database() as con:
        con.execute("INSERT INTO events(type,status,provider,detail,created) VALUES(?,?,?,?,?)",
                    (event_type[:80], status[:32], provider, detail[:500], utc_now()))


def active_conversation_id(con=None) -> str:
    if con is not None:
        row = con.execute("SELECT value FROM settings WHERE key='active_conversation'").fetchone()
        return row[0]
    with database() as db:
        row = db.execute("SELECT value FROM settings WHERE key='active_conversation'").fetchone()
        return row[0]


def load_agent_history():
    cid = active_conversation_id()
    with database() as con:
        rows = con.execute(
            "SELECT role,content,provider FROM messages WHERE conversation_id=? ORDER BY id DESC LIMIT 24",
            (cid,),
        ).fetchall()
    AGENT.history = [dict(role=r[0], content=r[1], provider=r[2])
                     for r in reversed(rows)]


def memory_context() -> str:
    with database() as con:
        rows = con.execute(
            "SELECT content,category,source_type FROM memories ORDER BY updated DESC LIMIT 30"
        ).fetchall()
    return "\n".join(f"[{r['source_type']}; {r['category']}] {r['content']}" for r in rows)


def ui_preferences() -> dict:
    with database() as con:
        rows = con.execute("SELECT key,value FROM settings WHERE key LIKE 'ui:%'").fetchall()
    return {row["key"][3:]: json.loads(row["value"]) for row in rows}


def provider_configuration() -> dict:
    return {
        "groq": {"configured": bool(os.getenv("GROQ_API_KEY")), "model": GROQ_MODEL,
                 "capability": "Technical chat and local tools"},
        "gemini": {"configured": bool(os.getenv("GEMINI_API_KEY")), "model": GEMINI_MODEL,
                   "capability": "General conversation"},
        "nemotron": {"configured": bool(os.getenv("OPENROUTER_API_KEY")),
                     "model": OPENROUTER_MODEL, "capability": "Web research via OpenRouter search"},
    }


def redact_payload(value):
    secrets = [os.getenv(name, "") for name in
               ("GROQ_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY")]
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, "[REDACTED]")
        return value
    if isinstance(value, list):
        return [redact_payload(item) for item in value]
    if isinstance(value, dict):
        return {key: redact_payload(item) for key, item in value.items()}
    return value


def index_counts() -> dict:
    result = {"filename_files": None, "filename_directories": None,
              "content_files": None, "filename_ready": pathindex.is_built(),
              "content_db_bytes": None}
    try:
        with closing(pathindex.connect()) as con:
            result["filename_files"] = con.execute(
                "SELECT COUNT(*) FROM paths WHERE is_dir=0").fetchone()[0]
            result["filename_directories"] = con.execute(
                "SELECT COUNT(*) FROM paths WHERE is_dir=1").fetchone()[0]
    except sqlite3.Error:
        pass
    try:
        with closing(indexer.connect()) as con:
            result["content_files"] = con.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        if indexer.DB_PATH.exists():
            result["content_db_bytes"] = indexer.DB_PATH.stat().st_size
    except sqlite3.Error:
        pass
    return result


def provider_health() -> dict:
    """Check provider API reachability on demand; never return request URLs or credentials."""
    statuses = {}
    configs = provider_configuration()
    urls = {
        "groq": ("https://api.groq.com/openai/v1/models", os.getenv("GROQ_API_KEY")),
        "gemini": ("https://generativelanguage.googleapis.com/v1beta/models?key="
                   + urllib.parse.quote(os.getenv("GEMINI_API_KEY", "")),
                   os.getenv("GEMINI_API_KEY")),
        "nemotron": ("https://openrouter.ai/api/v1/models", os.getenv("OPENROUTER_API_KEY")),
    }
    for name, (url, key) in urls.items():
        if not key:
            statuses[name] = {"configured": False, "reachable": False,
                              "message": "Credential not configured"}
            continue
        headers = {"Authorization": f"Bearer {key}"} if name != "gemini" else {}
        try:
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=8) as response:
                statuses[name] = {"configured": True, "reachable": response.status == 200,
                                  "message": "API responded successfully" if response.status == 200 else "API returned an error"}
        except urllib.error.HTTPError as exc:
            statuses[name] = {"configured": True, "reachable": False,
                              "message": f"Provider returned HTTP {exc.code}"}
        except Exception as exc:
            statuses[name] = {"configured": True, "reachable": False,
                              "message": f"Connection unavailable ({type(exc).__name__})"}
    return statuses


def confirm_in_ui(action: str) -> bool:
    confirmation_id = uuid.uuid4().hex
    record = {"id": confirmation_id, "action": action[:12000], "event": threading.Event(),
              "approved": False, "created": time.time()}
    with CONFIRM_LOCK:
        PENDING_CONFIRMATIONS[confirmation_id] = record
    event("file_approval", "pending", "A file operation is waiting for owner confirmation")
    record["event"].wait(180)
    with CONFIRM_LOCK:
        PENDING_CONFIRMATIONS.pop(confirmation_id, None)
    event("file_approval", "approved" if record["approved"] else "declined_or_expired",
          "Owner confirmation received" if record["approved"] else "File operation was not approved")
    return bool(record["approved"])


class Handler(BaseHTTPRequestHandler):
    server_version = "DEVILLocal/1.0"

    def log_message(self, fmt, *args):
        # Request bodies and query strings may contain private text or file paths.
        print(f"[DEVIL UI] {self.address_string()} {self.command} {self.path.split('?')[0]}")

    def _local_request_allowed(self) -> bool:
        host = self.headers.get("Host", "")
        origin = self.headers.get("Origin")
        return host in ALLOWED_HOSTS and (not origin or origin in ALLOWED_ORIGINS)

    def _json(self, status: int, payload):
        data = json.dumps(redact_payload(payload), ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'self'; connect-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self' 'wasm-unsafe-eval'; worker-src 'self' blob:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length < 0 or length > MAX_JSON_BYTES:
            raise ValueError("Request body exceeds the local API size limit.")
        raw = self.rfile.read(length)
        if not raw:
            return {}
        result = json.loads(raw.decode("utf-8"))
        if not isinstance(result, dict):
            raise ValueError("Request body must be a JSON object.")
        return result

    def _serve_ui(self, raw_path: str):
        path = urllib.parse.unquote(raw_path.split("?", 1)[0]).lstrip("/")
        if not path:
            path = "index.html"
        target = (UI_DIST / path).resolve()
        try:
            target.relative_to(UI_DIST.resolve())
        except ValueError:
            self._json(403, {"error": "Invalid asset path"})
            return
        if not target.is_file():
            target = UI_DIST / "index.html"
        if not target.exists():
            body = ("DEVIL UI is not built yet. Run `cd ui; pnpm install; pnpm build`.\n"
                    "During development, start `pnpm dev` and `python web_server.py`.").encode()
            self.send_response(503)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        content = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(target.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-cache" if target.name == "index.html" else "public, max-age=3600")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'self'; connect-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self' 'wasm-unsafe-eval'; worker-src 'self' blob:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(content)

    def do_OPTIONS(self):
        if not self._local_request_allowed():
            self._json(403, {"error": "Local requests only"})
            return
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", self.headers.get("Origin", ""))
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        if not self._local_request_allowed():
            self._json(403, {"error": "Local requests only"})
            return
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        try:
            if parsed.path == "/api/state":
                cid = active_conversation_id()
                with database() as con:
                    msg_count = con.execute("SELECT COUNT(*) FROM messages WHERE conversation_id=?", (cid,)).fetchone()[0]
                    memory_count = con.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
                    training = con.execute("SELECT status,COUNT(*) n FROM training_examples GROUP BY status").fetchall()
                    events = con.execute("SELECT type,status,provider,detail,created FROM events ORDER BY id DESC LIMIT 8").fetchall()
                    candidate_counts = {r["status"]: r["n"] for r in training}
                self._json(200, {
                    "identity": {"application": "DEVIL", "assistant": "Dexter", "owner": "Devil"},
                    "roots": [str(root) for root in policy.load_roots()],
                    "conversation_id": cid, "message_count": msg_count,
                    "providers": provider_configuration(), "indexes": index_counts(),
                    "preferences": ui_preferences(),
                    "index_job": index_jobs.snapshot(), "memory_count": memory_count,
                    "training": {"trainer": "not implemented", "status": "idle",
                                 "dataset_candidates": sum(candidate_counts.values()),
                                 "pending_review": candidate_counts.get("pending", 0),
                                 "accepted": candidate_counts.get("accepted", 0),
                                 "rejected": candidate_counts.get("rejected", 0),
                                 "metrics": None},
                    "voice": {"speech_to_text": bool(AGENT.groq),
                              "text_to_speech": os.name == "nt",
                              "mode": "push-to-talk"},
                    "recent_events": [dict(r) for r in events],
                })
            elif parsed.path == "/api/chat/history":
                cid = active_conversation_id()
                with database() as con:
                    rows = con.execute("SELECT id,role,content,provider,created,excluded_training FROM messages WHERE conversation_id=? ORDER BY id", (cid,)).fetchall()
                self._json(200, {"conversation_id": cid, "messages": [dict(r) for r in rows]})
            elif parsed.path == "/api/chat/conversations":
                with database() as con:
                    rows = con.execute("SELECT id,title,created,updated FROM conversations ORDER BY updated DESC LIMIT 50").fetchall()
                self._json(200, {"conversations": [dict(r) for r in rows], "active": active_conversation_id()})
            elif parsed.path == "/api/files/search":
                q = query.get("q", [""])[0].strip()[:300]
                mode = query.get("mode", ["name"])[0]
                if not q:
                    self._json(200, {"results": [], "indexes": index_counts()})
                elif mode == "content":
                    self._json(200, {"results": search.search_files(q, limit=15), "indexes": index_counts()})
                else:
                    self._json(200, {**pathindex.find(q, kind="any", limit=15), "indexes": index_counts()})
            elif parsed.path == "/api/files/read":
                path = query.get("path", [""])[0]
                result = run_local_tool("read_indexed_file", {"path": path})
                self._json(200 if "error" not in result else 400, result)
            elif parsed.path == "/api/jobs":
                self._json(200, {"job": index_jobs.snapshot()})
            elif parsed.path == "/api/providers/health":
                self._json(200, {"providers": provider_health()})
            elif parsed.path == "/api/confirmations":
                with CONFIRM_LOCK:
                    current = [{"id": v["id"], "action": v["action"]}
                               for v in PENDING_CONFIRMATIONS.values()]
                self._json(200, {"pending": current})
            elif parsed.path == "/api/memories":
                term = query.get("q", [""])[0].strip()[:200]
                with database() as con:
                    if term:
                        rows = con.execute("SELECT * FROM memories WHERE content LIKE ? OR category LIKE ? ORDER BY updated DESC", (f"%{term}%", f"%{term}%")).fetchall()
                    else:
                        rows = con.execute("SELECT * FROM memories ORDER BY updated DESC").fetchall()
                self._json(200, {"memories": [dict(r) for r in rows]})
            elif parsed.path == "/api/training/candidates":
                with database() as con:
                    rows = con.execute("SELECT * FROM training_examples ORDER BY updated DESC").fetchall()
                self._json(200, {"candidates": [dict(r) for r in rows]})
            elif parsed.path == "/api/training/events":
                filters = {k: query.get(k, [""])[0][:80] for k in ("type", "provider", "status")}
                sql = "SELECT * FROM events WHERE 1=1"
                args = []
                for key, val in filters.items():
                    if val:
                        sql += f" AND {key}=?"
                        args.append(val)
                sql += " ORDER BY id DESC LIMIT 200"
                with database() as con:
                    rows = con.execute(sql, args).fetchall()
                self._json(200, {"events": [dict(r) for r in rows]})
            elif parsed.path == "/api/settings":
                with database() as con:
                    rows = con.execute("SELECT key,value FROM settings WHERE key LIKE 'ui:%'").fetchall()
                self._json(200, {"settings": {r["key"][3:]: json.loads(r["value"]) for r in rows}})
            else:
                self._serve_ui(self.path)
        except Exception as exc:
            self._json(500, {"error": f"{type(exc).__name__}: {str(exc)[:300]}"})

    def do_POST(self):
        if not self._local_request_allowed():
            self._json(403, {"error": "Local requests only"})
            return
        try:
            body = self._body()
            path = urllib.parse.urlparse(self.path).path
            if path == "/api/chat":
                message = body.get("message")
                provider = body.get("provider", "auto")
                if not isinstance(message, str) or not message.strip() or len(message) > 10000:
                    self._json(400, {"error": "Message must contain 1–10,000 characters."})
                    return
                if provider not in {"auto", "groq", "gemini", "nemotron"}:
                    self._json(400, {"error": "Unsupported provider selection."})
                    return
                with CHAT_LOCK:
                    cid = active_conversation_id()
                    AGENT.owner_memory_context = memory_context()
                    before = len(AGENT.history)
                    display_provider, answer = AGENT.ask(message.strip(), provider=provider)
                    if len(AGENT.history) == before:
                        AGENT.history.extend([
                            {"role": "user", "content": message.strip()},
                            {"role": "assistant", "content": answer, "provider": display_provider},
                        ])
                        AGENT.history = AGENT.history[-24:]
                    now = utc_now()
                    with database() as con:
                        count = con.execute("SELECT COUNT(*) FROM messages WHERE conversation_id=?", (cid,)).fetchone()[0]
                        if count == 0:
                            con.execute("UPDATE conversations SET title=?,updated=? WHERE id=?",
                                        (message.strip()[:72], now, cid))
                        user_id = con.execute("INSERT INTO messages(conversation_id,role,content,created) VALUES(?,?,?,?)",
                                              (cid, "user", message.strip(), now)).lastrowid
                        assistant_id = con.execute("INSERT INTO messages(conversation_id,role,content,provider,created) VALUES(?,?,?,?,?)",
                                                   (cid, "assistant", answer, display_provider, utc_now())).lastrowid
                        con.execute("UPDATE conversations SET updated=? WHERE id=?", (utc_now(), cid))
                    event("chat", "completed" if display_provider != "Unavailable" else "failed",
                          "Assistant response completed" if display_provider != "Unavailable" else "No provider completed the request",
                          display_provider)
                self._json(200, {"conversation_id": cid,
                                 "message": {"id": assistant_id, "role": "assistant",
                                             "content": answer, "provider": display_provider,
                                             "created": utc_now()},
                                 "user_message_id": user_id})
            elif path == "/api/chat/compare":
                message = body.get("message")
                if not isinstance(message, str) or not message.strip() or len(message) > 10000:
                    self._json(400, {"error": "Message must contain 1–10,000 characters."})
                    return
                providers = ("groq", "gemini", "nemotron")
                results = []
                with CHAT_LOCK:
                    cid = active_conversation_id()
                    context = memory_context()
                    for selected in providers:
                        trial = copy.copy(AGENT)
                        trial.history = list(AGENT.history)
                        trial.owner_memory_context = context
                        label, answer = trial.ask(message.strip(), provider=selected)
                        results.append({"provider": label, "selection": selected,
                                        "content": answer,
                                        "available": label != "Unavailable"})
                    now = utc_now()
                    with database() as con:
                        count = con.execute("SELECT COUNT(*) FROM messages WHERE conversation_id=?", (cid,)).fetchone()[0]
                        if count == 0:
                            con.execute("UPDATE conversations SET title=?,updated=? WHERE id=?",
                                        (message.strip()[:72], now, cid))
                        user_message_id = con.execute("INSERT INTO messages(conversation_id,role,content,created) VALUES(?,?,?,?)",
                                                      (cid, "user", message.strip(), now)).lastrowid
                        for item in results:
                            if item["available"]:
                                item["message_id"] = con.execute(
                                    "INSERT INTO messages(conversation_id,role,content,provider,created) VALUES(?,?,?,?,?)",
                                    (cid, "assistant", item["content"], item["provider"], utc_now()),
                                ).lastrowid
                                item["paired_user_id"] = user_message_id
                    AGENT.history.append({"role": "user", "content": message.strip()})
                    AGENT.history.extend({"role": "assistant", "content": r["content"],
                                          "provider": r["provider"]}
                                         for r in results if r["available"])
                    AGENT.history = AGENT.history[-24:]
                for item in results:
                    event("provider_comparison", "completed" if item["available"] else "unavailable",
                          "Provider comparison request finished", item["provider"])
                self._json(200, {"conversation_id": cid, "responses": results})
            elif path == "/api/chat/new":
                cid = uuid.uuid4().hex
                now = utc_now()
                with database() as con:
                    con.execute("INSERT INTO conversations VALUES(?,?,?,?)", (cid, "New conversation", now, now))
                    con.execute("INSERT OR REPLACE INTO settings VALUES('active_conversation',?)", (cid,))
                with CHAT_LOCK:
                    AGENT.history = []
                self._json(200, {"conversation_id": cid, "messages": []})
            elif path == "/api/chat/select":
                cid = body.get("conversation_id", "")
                with database() as con:
                    exists = con.execute("SELECT 1 FROM conversations WHERE id=?", (cid,)).fetchone()
                    if not exists:
                        self._json(404, {"error": "Conversation not found."})
                        return
                    con.execute("INSERT OR REPLACE INTO settings VALUES('active_conversation',?)", (cid,))
                with CHAT_LOCK:
                    load_agent_history()
                self._json(200, {"conversation_id": cid})
            elif path == "/api/index":
                kind = body.get("kind", "all")
                if kind == "all":
                    result = index_jobs.start("project content indexing", index_projects)
                elif kind == "names":
                    result = index_jobs.start("filename scan", lambda cancel, progress:
                                               pathindex.scan(on_progress=progress, cancel_event=cancel))
                elif kind == "project":
                    folder = str(body.get("path", ""))
                    if not policy.is_allowed(folder, is_dir=True) or not Path(folder).is_dir():
                        self._json(400, {"error": "Choose an existing folder inside the configured DEVIL roots."})
                        return
                    result = index_jobs.start(f"content index: {folder}",
                                              lambda cancel, progress: indexer.index_folder(
                                                  folder, cancel_event=cancel, on_progress=progress))
                else:
                    self._json(400, {"error": "Unknown index job."})
                    return
                self._json(200 if "error" not in result else 409, result)
            elif path == "/api/index/cancel":
                self._json(200, index_jobs.cancel())
            elif path == "/api/confirmations/answer":
                cid = str(body.get("id", ""))
                with CONFIRM_LOCK:
                    record = PENDING_CONFIRMATIONS.get(cid)
                    if not record:
                        self._json(404, {"error": "Confirmation expired or not found."})
                        return
                    record["approved"] = body.get("approve") is True
                    record["event"].set()
                self._json(200, {"resolved": True})
            elif path == "/api/voice/transcribe":
                if not AGENT.groq:
                    self._json(503, {"error": "Speech recognition needs a configured Groq credential."})
                    return
                encoded = body.get("audio_base64", "")
                mime = body.get("mime_type", "audio/webm")
                if not isinstance(encoded, str) or len(encoded) > 14_000_000:
                    self._json(400, {"error": "Audio payload exceeds the size limit."})
                    return
                raw = base64.b64decode(encoded, validate=True)
                transcript = transcribe_audio(AGENT.groq, raw, mime)
                event("voice_transcription", "completed", "Push-to-talk audio transcribed", "Groq / Whisper")
                self._json(200, {"transcript": transcript})
            elif path == "/api/voice/speak":
                text = body.get("text", "")
                provider = body.get("provider", "")
                if os.name != "nt":
                    self._json(503, {"error": "Windows SAPI speech output is unavailable on this platform."})
                    return
                if not isinstance(text, str) or not text.strip() or len(text) > 3000:
                    self._json(400, {"error": "Speech text must contain 1–3,000 characters."})
                    return
                speak_reply(text, provider=provider)
                event("voice_playback", "completed", "Windows SAPI finished speaking", provider[:80])
                self._json(200, {"spoken": True})
            elif path == "/api/memories":
                content = body.get("content", "").strip() if isinstance(body.get("content"), str) else ""
                category = body.get("category", "general")
                source_type = body.get("source_type", "confirmed")
                confidence = body.get("confidence", 1.0 if source_type == "confirmed" else 0.5)
                if not content or len(content) > 2000 or len(category) > 60 or source_type not in {"confirmed", "inference"}:
                    self._json(400, {"error": "Memory needs text (max 2,000 characters), a short category, and confirmed/inference source."})
                    return
                if SECRET_VALUE_RE.search(content):
                    self._json(400, {"error": "This looks like it contains a secret. It was not saved."})
                    return
                confidence = max(0.0, min(float(confidence), 1.0))
                now = utc_now()
                with database() as con:
                    mid = con.execute("INSERT INTO memories(content,category,source_type,confidence,created,updated) VALUES(?,?,?,?,?,?)",
                                      (content, category, source_type, confidence, now, now)).lastrowid
                event("memory", "saved", "Owner explicitly saved a memory")
                self._json(201, {"id": mid, "content": content, "category": category,
                                 "source_type": source_type, "confidence": confidence,
                                 "created": now, "updated": now})
            elif path == "/api/training/candidates":
                self._create_candidate(body)
            elif path.startswith("/api/training/candidates/"):
                candidate_id = int(path.rsplit("/", 1)[1])
                self._update_candidate(candidate_id, body)
            elif path.startswith("/api/messages/") and path.endswith("/exclusion"):
                message_id = int(path.split("/")[3])
                excluded = body.get("excluded") is True
                with database() as con:
                    cur = con.execute("UPDATE messages SET excluded_training=? WHERE id=?", (int(excluded), message_id))
                    if not cur.rowcount:
                        self._json(404, {"error": "Message not found."})
                        return
                    if excluded:
                        con.execute("DELETE FROM training_examples WHERE message_id=?", (message_id,))
                event("training_exclusion", "excluded" if excluded else "included",
                      "Owner changed training eligibility; selected examples were removed when excluded")
                self._json(200, {"message_id": message_id, "excluded_training": excluded})
            elif path.startswith("/api/memories/"):
                self._update_memory(int(path.rsplit("/", 1)[1]), body)
            elif path == "/api/settings":
                allowed = {"tts_enabled", "reduced_motion", "provider"}
                for key, value in body.items():
                    if key not in allowed:
                        continue
                    with database() as con:
                        con.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)",
                                    (f"ui:{key}", json.dumps(value)))
                self._json(200, {"saved": True})
            else:
                self._json(404, {"error": "Unknown local API endpoint"})
        except (ValueError, json.JSONDecodeError) as exc:
            self._json(400, {"error": str(exc)[:300]})
        except Exception as exc:
            event("api", "failed", f"{type(exc).__name__}: request failed")
            self._json(500, {"error": f"{type(exc).__name__}: {str(exc)[:300]}"})

    def _create_candidate(self, body):
        message_id = int(body.get("message_id", 0))
        with database() as con:
            assistant = con.execute("SELECT * FROM messages WHERE id=? AND role='assistant'", (message_id,)).fetchone()
            if not assistant:
                self._json(404, {"error": "Choose an assistant response first."})
                return
            user = con.execute("SELECT * FROM messages WHERE conversation_id=? AND role='user' AND id<? ORDER BY id DESC LIMIT 1",
                               (assistant["conversation_id"], message_id)).fetchone()
            if not user:
                self._json(400, {"error": "The assistant response has no matching user message."})
                return
            if SECRET_VALUE_RE.search(user["content"] + "\n" + assistant["content"]):
                self._json(400, {"error": "This conversation appears to contain a secret and cannot be selected."})
                return
            now = utc_now()
            # This endpoint is the owner's explicit selection action. Mark the
            # exact pair eligible only after secret checks and candidate insert.
            con.execute("UPDATE messages SET excluded_training=0 WHERE id IN (?,?)",
                        (user["id"], assistant["id"]))
            eid = con.execute("INSERT INTO training_examples(message_id,prompt,response,teacher,category,status,created,updated) VALUES(?,?,?,?,?,'pending',?,?)",
                              (message_id, user["content"], assistant["content"], assistant["provider"] or "unknown",
                               str(body.get("category", "conversation"))[:60], now, now)).lastrowid
        event("training_candidate", "pending_review", "Owner explicitly submitted an example for review", assistant["provider"])
        self._json(201, {"id": eid, "status": "pending"})

    def _update_candidate(self, candidate_id: int, body: dict):
        status = body.get("status")
        if status not in {"pending", "accepted", "rejected"}:
            self._json(400, {"error": "Candidate status must be pending, accepted, or rejected."})
            return
        now = utc_now()
        with database() as con:
            row = con.execute("SELECT * FROM training_examples WHERE id=?", (candidate_id,)).fetchone()
            if not row:
                self._json(404, {"error": "Candidate not found."})
                return
            prompt = body.get("prompt", row["prompt"])
            response = body.get("response", row["response"])
            if not isinstance(prompt, str) or not isinstance(response, str) or len(prompt) > 10000 or len(response) > 10000:
                self._json(400, {"error": "Candidate text exceeds the 10,000 character limit."})
                return
            if SECRET_VALUE_RE.search(prompt + "\n" + response):
                self._json(400, {"error": "This example appears to contain a secret."})
                return
            con.execute("UPDATE training_examples SET prompt=?,response=?,status=?,updated=? WHERE id=?",
                        (prompt, response, status, now, candidate_id))
        event("training_candidate", status, "Owner reviewed a training candidate")
        self._json(200, {"id": candidate_id, "status": status, "updated": now})

    def _update_memory(self, memory_id: int, body: dict):
        if not body:
            with database() as con:
                cur = con.execute("DELETE FROM memories WHERE id=?", (memory_id,))
                if not cur.rowcount:
                    self._json(404, {"error": "Memory not found."})
                    return
            event("memory", "deleted", "Owner deleted a saved memory")
            self._json(200, {"deleted": True, "id": memory_id})
            return
        if self.command == "DELETE":
            with database() as con:
                cur = con.execute("DELETE FROM memories WHERE id=?", (memory_id,))
                if not cur.rowcount:
                    self._json(404, {"error": "Memory not found."})
                    return
            event("memory", "deleted", "Owner deleted a saved memory")
            self._json(200, {"deleted": True, "id": memory_id})
            return
        content = body.get("content")
        category = body.get("category", "general")
        source_type = body.get("source_type", "confirmed")
        confidence = max(0.0, min(float(body.get("confidence", 1.0)), 1.0))
        if not isinstance(content, str) or not content.strip() or len(content) > 2000 or source_type not in {"confirmed", "inference"}:
            self._json(400, {"error": "Invalid memory update."})
            return
        if SECRET_VALUE_RE.search(content):
            self._json(400, {"error": "This looks like it contains a secret. It was not saved."})
            return
        now = utc_now()
        with database() as con:
            cur = con.execute("UPDATE memories SET content=?,category=?,source_type=?,confidence=?,updated=? WHERE id=?",
                              (content.strip(), category[:60], source_type, confidence, now, memory_id))
            if not cur.rowcount:
                self._json(404, {"error": "Memory not found."})
                return
        event("memory", "updated", "Owner edited a saved memory")
        self._json(200, {"updated": True, "id": memory_id})

    def do_PUT(self):
        self.do_POST()

    def do_DELETE(self):
        if not self._local_request_allowed():
            self._json(403, {"error": "Local requests only"})
            return
        try:
            path = urllib.parse.urlparse(self.path).path
            if path.startswith("/api/memories/"):
                self._update_memory(int(path.rsplit("/", 1)[1]), {})
            elif path.startswith("/api/training/candidates/"):
                candidate_id = int(path.rsplit("/", 1)[1])
                with database() as con:
                    cur = con.execute("DELETE FROM training_examples WHERE id=?", (candidate_id,))
                    if not cur.rowcount:
                        self._json(404, {"error": "Training candidate not found."})
                        return
                event("training_candidate", "deleted", "Owner deleted a training candidate")
                self._json(200, {"deleted": True, "id": candidate_id})
            else:
                self._json(404, {"error": "Unknown local API endpoint"})
        except Exception as exc:
            self._json(400, {"error": f"{type(exc).__name__}: {str(exc)[:250]}"})


def main():
    if not ipaddress.ip_address(HOST).is_loopback:
        raise RuntimeError("DEVIL UI backend must bind to a loopback address.")
    initialize_database()
    load_agent_history()
    AGENT.owner_memory_context = memory_context()
    file_ops.CONFIRM_HANDLER = confirm_in_ui
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    server.daemon_threads = True
    print(f"DEVIL / Dexter UI listening on http://{HOST}:{PORT}")
    print("Configured providers:", ", ".join(k for k, v in provider_configuration().items() if v["configured"]) or "none")
    if not UI_DIST.joinpath("index.html").exists():
        print("UI assets are not built. In ui/, run: pnpm install && pnpm build")
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        print("\nDEVIL UI shutting down")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
