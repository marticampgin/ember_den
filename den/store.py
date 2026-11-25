"""SQLite persistence. Each operation owns its connection and transaction."""

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


class ConflictError(ValueError):
    pass


def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def new_id():
    return uuid.uuid4().hex


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY, value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT REFERENCES conversations(id) ON DELETE CASCADE,
                    created_at TEXT NOT NULL, source TEXT NOT NULL, status TEXT NOT NULL,
                    prompt TEXT NOT NULL, model TEXT NOT NULL DEFAULT '',
                    metrics TEXT NOT NULL DEFAULT '{}', snapshot TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS runs_by_date ON runs(created_at DESC);
                CREATE INDEX IF NOT EXISTS conversation_runs ON runs(conversation_id);
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    role TEXT NOT NULL, content TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS messages_by_run ON messages(run_id);
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY, text TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS run_feedback (
                    run_id TEXT PRIMARY KEY REFERENCES runs(id) ON DELETE CASCADE,
                    rating TEXT NOT NULL, note TEXT NOT NULL,
                    memory_id TEXT REFERENCES memories(id) ON DELETE SET NULL
                );
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def recover(self):
        with self.connect() as db:
            unfinished = db.execute("SELECT id, source, snapshot FROM runs WHERE status='running'").fetchall()
            for run in unfinished:
                snapshot = json.loads(run["snapshot"])
                snapshot["error"] = "Interrupted when Ember Den stopped."
                snapshot.setdefault("events", []).append({"stage": "Interrupted", "detail": snapshot["error"],
                                                          "elapsed_ms": None})
                db.execute("UPDATE runs SET status='error', snapshot=? WHERE id=?",
                           (json.dumps(snapshot), run["id"]))
                if run["source"] == "chat" and snapshot.get("response"):
                    db.execute("INSERT INTO messages(run_id,role,content) VALUES(?,'assistant',?)",
                               (run["id"], snapshot["response"]))

    def settings(self):
        with self.connect() as db:
            return {r["key"]: json.loads(r["value"]) for r in db.execute("SELECT * FROM settings")}

    def save_settings(self, changes):
        with self.connect() as db:
            db.executemany("INSERT OR REPLACE INTO settings VALUES (?, ?)",
                           [(key, json.dumps(value)) for key, value in changes.items()])

    def memories(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM memories ORDER BY updated_at DESC, rowid DESC")]

    def feedback(self, run_id, changes=None):
        with self.connect() as db:
            if changes is not None:
                db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM runs WHERE id=?", (run_id,)).fetchone():
                raise KeyError("Run not found.")
            row=db.execute("SELECT rating,note,memory_id FROM run_feedback WHERE run_id=?", (run_id,)).fetchone()
            current=dict(row) if row else {"rating":None,"note":"","memory_id":None}
            if changes is None:
                return current
            rating, note, remember = changes.get("rating"),changes.get("note",""),changes.get("remember",False)
            if rating not in ("helpful","unhelpful") or not isinstance(note,str) or len(note)>2000:
                raise ValueError("Choose a rating and enter up to 2,000 characters of feedback.")
            if type(remember) is not bool or (remember and not note.strip()):
                raise ValueError("Enter a preference before saving it to memory.")
            memory_id=current["memory_id"]
            if remember and not memory_id:
                memory_id=new_id()
                db.execute("INSERT INTO memories VALUES (?,?,?)", (memory_id,note.strip(),utcnow()))
            db.execute("INSERT OR REPLACE INTO run_feedback VALUES (?,?,?,?)",(run_id,rating,note.strip(),memory_id))
            return {"rating":rating,"note":note.strip(),"memory_id":memory_id}

    def save_memory(self, text, memory_id=None):
        when = utcnow()
        with self.connect() as db:
            if memory_id is None:
                memory_id = new_id()
                db.execute("INSERT INTO memories VALUES (?, ?, ?)", (memory_id, text, when))
            elif not db.execute("UPDATE memories SET text=?, updated_at=? WHERE id=?",
                                (text, when, memory_id)).rowcount:
                raise KeyError("Memory not found.")
        return {"id": memory_id, "text": text, "updated_at": when}

    def delete_memory(self, memory_id):
        with self.connect() as db:
            if not db.execute("DELETE FROM memories WHERE id=?", (memory_id,)).rowcount:
                raise KeyError("Memory not found.")

    def conversations(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute(
                "SELECT * FROM conversations ORDER BY updated_at DESC, rowid DESC")]

    def conversation(self, conversation_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM conversations WHERE id=?", (conversation_id,)).fetchone()
            if row is None:
                raise KeyError("Conversation not found.")
            messages = db.execute("""SELECT role, content, run_id, status, runs.created_at AS created_at,
                json_extract(runs.snapshot,'$.error') AS error
                FROM messages JOIN runs ON messages.run_id=runs.id
                WHERE runs.conversation_id=? ORDER BY messages.id""", (conversation_id,))
            return {**dict(row), "messages": [dict(m) for m in messages]}

    def delete_conversation(self, conversation_id):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self.assert_ready(db, conversation_id)
            if not db.execute("DELETE FROM conversations WHERE id=?", (conversation_id,)).rowcount:
                raise KeyError("Conversation not found.")

    @staticmethod
    def assert_ready(db, conversation_id):
        row = db.execute("SELECT 1 FROM runs WHERE conversation_id=? AND status='running'",
                         (conversation_id,)).fetchone()
        if row:
            raise ConflictError("A reply is already in progress in this conversation.")

    def begin(self, prompt, trace_prompt, source, conversation_id, config):
        run_id, created = new_id(), utcnow()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if source == "chat":
                title = " ".join(trace_prompt.split())
                if conversation_id is None:
                    conversation_id = new_id()
                    db.execute("INSERT INTO conversations VALUES (?, ?, ?)",
                               (conversation_id, title[:72], created))
                else:
                    self.assert_ready(db, conversation_id)
                    if not db.execute("UPDATE conversations SET updated_at=? WHERE id=?",
                                      (created, conversation_id)).rowcount:
                        raise KeyError("Conversation not found.")
            db.execute("""INSERT INTO runs(id,conversation_id,created_at,source,status,prompt,model,snapshot)
                          VALUES(?,?,?,?,'running',?,?,?)""",
                       (run_id, conversation_id, created, source, trace_prompt, config["model"],
                        json.dumps({"config": config})))
            if source == "chat":
                db.execute("INSERT INTO messages(run_id,role,content) VALUES(?,'user',?)",
                           (run_id, prompt))
        return run_id, conversation_id

    def recent_messages(self, conversation_id):
        with self.connect() as db:
            rows = db.execute("""SELECT role, content FROM messages JOIN runs ON runs.id=messages.run_id
                WHERE runs.conversation_id=? AND runs.status='completed'
                ORDER BY messages.id DESC LIMIT 32""", (conversation_id,)).fetchall()
            return [dict(r) for r in reversed(rows)]

    def finish(self, run_id, status, model, metrics, snapshot, answer):
        with self.connect() as db:
            db.execute("UPDATE runs SET status=?, model=?, metrics=?, snapshot=? WHERE id=?",
                       (status, model, json.dumps(metrics), json.dumps(snapshot), run_id))
            if answer is not None:
                db.execute("INSERT INTO messages(run_id,role,content) VALUES(?,'assistant',?)",
                           (run_id, answer))

    def checkpoint(self, run_id, model, metrics, snapshot):
        with self.connect() as db:
            db.execute("UPDATE runs SET model=?, metrics=?, snapshot=? WHERE id=? AND status='running'",
                       (model, json.dumps(metrics), json.dumps(snapshot), run_id))

    def runs(self):
        with self.connect() as db:
            rows = db.execute("""SELECT id, prompt, model, status, source, created_at, metrics
                                FROM runs ORDER BY created_at DESC, rowid DESC LIMIT 200""")
            return [{**dict(r), "metrics": json.loads(r["metrics"])} for r in rows]

    def run(self, run_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                raise KeyError("Run not found.")
            result = dict(row)
            result["metrics"] = json.loads(result["metrics"])
            result.update(json.loads(result.pop("snapshot")))
            return result
