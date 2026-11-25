"""Persistent, sequential comparisons against the same captured input."""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
import threading
import uuid

from .engine import protected, validate_config
from .evaluation import Evaluation, check_answer


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _text(value, label, maximum, required=False):
    if not isinstance(value, str) or len(value) > maximum or (required and not value.strip()):
        raise ValueError(f"{label} must be text of up to {maximum:,} characters.")
    return value


def _configuration(raw):
    if not isinstance(raw, dict):
        raise ValueError("Each configuration must be an object.")
    _text(raw.get("model"), "Model", 240, required=True)
    return validate_config(raw)


def _stored_snapshot(snapshot):
    stored = protected(snapshot)
    stored["redacted"] = stored != snapshot
    return stored


def changed_pass_revisions(results, installed):
    """Check the actual models a prior execution used, including routed and fallback passes."""
    changes = []
    for arm in results:
        run = arm.get("run") or {}
        for step in run.get("workflow_passes", []):
            model = step.get("model")
            original = step.get("digest")
            if not model or not original:
                continue
            present = model in installed
            current = installed[model].get("digest") if present else None
            if original != current:
                changes.append({"arm": arm["label"], "role": step["role"], "model": model,
                                "previous_digest": original, "current_digest": current, "available": present})
    return changes


def _snapshot(raw):
    if not isinstance(raw, dict):
        raise ValueError("A comparison requires a JSON object.")
    configs = raw.get("configs", [])
    if not isinstance(configs, list) or len(configs) != 2:
        raise ValueError("Choose two configurations to compare.")
    context = raw.get("context", [])
    history = raw.get("history", [])
    if not isinstance(context, list) or len(context) > 64 or not all(
        isinstance(item, dict) and isinstance(item.get("text"), str) and len(item["text"]) <= 16000
        for item in context
    ):
        raise ValueError("Invalid memory snapshot.")
    if not isinstance(history, list) or len(history) > 256 or not all(
        isinstance(item, dict) and item.get("role") in {"assistant", "user"}
        and isinstance(item.get("content"), str) and len(item["content"]) <= 64000
        for item in history
    ):
        raise ValueError("Invalid conversation snapshot.")
    source = raw.get("source_run_id")
    if source is not None:
        _text(source, "Source run", 128, required=True)
    check_mode = raw.get("check_mode", "contains")
    if check_mode not in ("contains", "exact", "json", "source"):
        raise ValueError("Unknown evaluation check.")
    return {
        "check_mode": check_mode,
        "live_retrieval": raw.get("live_retrieval") is True,
        "prompt": _text(raw.get("prompt"), "Prompt", 12000, True),
        "expected": _text(raw.get("expected", ""), "Expected text", 2000).strip(),
        "configs": [_configuration(config) for config in configs],
        "context": deepcopy(context),
        "history": [{"role": item["role"], "content": item["content"]} for item in history],
        "source_run_id": source,
        "source_redacted": raw.get("source_redacted") is True or raw.get("redacted") is True,
    }


class Laboratory:
    """Lab requests have their own snapshots and never update chat configuration."""

    def __init__(self, db_path: Path, run_config, inventory):
        self.db_path = db_path
        self.run_config = run_config
        self.inventory = inventory
        self._session = uuid.uuid4().hex
        self._busy = threading.Lock()
        with self._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS lab_experiments (
                    id TEXT PRIMARY KEY, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    session_id TEXT NOT NULL, prompt TEXT NOT NULL, status TEXT NOT NULL,
                    snapshot TEXT NOT NULL, results TEXT NOT NULL, replay_of TEXT
                );
                CREATE INDEX IF NOT EXISTS lab_experiments_recent ON lab_experiments(created_at DESC);
                CREATE TABLE IF NOT EXISTS lab_cases (
                    id TEXT PRIMARY KEY, created_at TEXT NOT NULL, name TEXT NOT NULL,
                    snapshot TEXT NOT NULL
                );
            """)

        self.evaluation = Evaluation(self)

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.db_path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def _summary(self, row):
        status = row["status"]
        if status == "running" and row["session_id"] != self._session:
            status = "interrupted"
        return {**{key: row[key] for key in ("id", "created_at", "updated_at", "prompt", "replay_of")},
                "status": status}

    def experiment(self, identifier):
        with self._db() as db:
            row = db.execute("SELECT * FROM lab_experiments WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise KeyError("Comparison does not exist.")
        summary = self._summary(row)
        results = json.loads(row["results"])
        if summary["status"] == "interrupted":
            for result in results:
                if result["status"] in {"running", "pending"}:
                    result.update(status="interrupted", error="The local server stopped before this run finished.")
        with self._db() as db:
            review = db.execute("SELECT preference FROM lab_reviews WHERE experiment_id=?", (identifier,)).fetchone()
            judgment = db.execute("SELECT model,run_id,assessment FROM lab_judgments WHERE experiment_id=?",
                                  (identifier,)).fetchone()
        return {**summary, "snapshot": json.loads(row["snapshot"]), "results": results,
                "preference": review["preference"] if review else None, "judgment": dict(judgment) if judgment else None}

    def _update(self, identifier, status, results):
        with self._db() as db:
            db.execute("UPDATE lab_experiments SET status=?, results=?, updated_at=? WHERE id=?",
                           (status, json.dumps(protected(results)), _now(), identifier))

    def _execute(self, identifier, snapshot, results, release=True, frozen=None):
        try:
            for index, config in enumerate(snapshot["configs"]):
                result = results[index]
                result["status"] = "running"
                self._update(identifier, "running", results)
                try:
                    context = None if snapshot.get("live_retrieval") else snapshot["context"]
                    if frozen is not None:
                        context = frozen[index]
                    run = self.run_config(snapshot["prompt"], deepcopy(config),
                                          context=deepcopy(context), history=deepcopy(snapshot["history"]))
                    result["run"] = run
                    result["status"] = "completed" if run["status"] == "completed" else "failed"
                    result["error"] = run.get("error") or None
                    if result["status"] == "completed":
                        result["check"] = check_answer(snapshot.get("check_mode","contains"), snapshot["expected"], run)
                except (ValueError, RuntimeError, OSError) as error:
                    result.update(status="failed", error=str(error))
                self._update(identifier, "running", results)
            count = sum(result["status"] == "completed" for result in results)
            self._update(identifier, ("failed", "partial", "completed")[count], results)
        except Exception:
            # Keep completed arms visible if an unexpected provider/worker error interrupts the pair.
            for result in results:
                if result["status"] in {"pending", "running"}:
                    result.update(status="failed", error="Comparison interrupted by an internal error.")
            status = "partial" if any(r["status"] == "completed" for r in results) else "failed"
            self._update(identifier, status, results)
            raise
        finally:
            if release:
                self._busy.release()

    def _start(self, snapshot, *, previous=None, in_suite=False, frozen=None):
        if not in_suite and not self._busy.acquire(blocking=False):
            return 409, {"error": "A comparison is already running. Open History to follow its progress."}
        started = False
        try:
            try:
                installed = {model["name"]: model for model in self.inventory()}
            except (RuntimeError, OSError):
                return 503, {"error": "The local model server is unavailable. Start Ollama and retry."}
            for config in snapshot["configs"]:
                if config["model"] not in installed:
                    return 400, {"error": f"The local model {config['model']} is unavailable."}
            snapshot["models"] = [deepcopy(installed[config["model"]]) for config in snapshot["configs"]]
            if previous:
                roles = changed_pass_revisions(previous["results"], installed)
                if roles:
                    snapshot["changed_roles"] = roles
                previous_models = previous["snapshot"].get("models", [])
                changed = [label for label, old, new in zip(("A", "B"), previous_models, snapshot["models"])
                           if old.get("digest") != new.get("digest")]
                if changed:
                    snapshot["changed_models"] = changed
            stored = _stored_snapshot(snapshot)
            identifier, created_at = uuid.uuid4().hex, _now()
            results = [{"label": label, "status": "pending", "run": None, "check": None, "error": None}
                       for label in ("A", "B")]
            with self._db() as db:
                db.execute("INSERT INTO lab_experiments VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                           (identifier, created_at, created_at, self._session, stored["prompt"], "running",
                            json.dumps(stored), json.dumps(results), previous["id"] if previous else None))
            if in_suite:
                self._execute(identifier, snapshot, results, release=False)
            else:
                threading.Thread(target=self._execute, args=(identifier, snapshot, results, True, frozen),
                                 name="lab-comparison", daemon=True).start()
            started = True
        finally:
            if not started and not in_suite:
                self._busy.release()
        return 202, {"id": identifier}

    def route(self, method, path, body=None):
        extension = self.evaluation.route(method, path, body)
        if extension is not None:
            return extension
        if path == "/api/lab/experiments":
            if method == "POST":
                return self._start(_snapshot(body))
            if method == "GET":
                with self._db() as db:
                    rows = db.execute("""
                        SELECT id, created_at, updated_at, prompt, replay_of, session_id, status
                        FROM lab_experiments ORDER BY created_at DESC, rowid DESC LIMIT 100
                    """).fetchall()
                return 200, {"experiments": [self._summary(row) for row in rows]}
        match = re.fullmatch(r"/api/lab/experiments/([a-f0-9]{32})(/replay)?", path)
        if match:
            experiment = self.experiment(match[1])
            if method == "GET" and not match[2]:
                return 200, experiment
            if method == "POST" and match[2]:
                refs = [(arm.get("run") or {}).get("context", experiment["snapshot"]["context"])
                        for arm in experiment["results"]]
                return self._start(_snapshot(experiment["snapshot"]), previous=experiment, frozen=refs)
        if path == "/api/lab/cases":
            if method == "POST":
                snapshot = _snapshot(body)
                name = _text(body.get("name"), "Case name", 100, True).strip()
                identifier = uuid.uuid4().hex
                with self._db() as db:
                    db.execute("INSERT INTO lab_cases VALUES (?, ?, ?, ?)",
                               (identifier, _now(), protected(name), json.dumps(_stored_snapshot(snapshot))))
                return 201, {"id": identifier}
            if method == "GET":
                with self._db() as db:
                    rows = db.execute("SELECT id, created_at, name FROM lab_cases ORDER BY created_at DESC, rowid DESC LIMIT 100").fetchall()
                return 200, {"cases": [dict(row) for row in rows]}
        match = re.fullmatch(r"/api/lab/cases/([a-f0-9]{32})", path)
        if match and method in {"GET", "DELETE"}:
            with self._db() as db:
                row = db.execute("SELECT * FROM lab_cases WHERE id=?", (match[1],)).fetchone()
                if row is None:
                    raise KeyError("Test case does not exist.")
                if method == "DELETE":
                    db.execute("DELETE FROM lab_cases WHERE id=?", (match[1],))
                    return 200, {"id": match[1]}
            snapshot = json.loads(row["snapshot"])
            if "redacted" not in snapshot:
                # Cases saved by the first release contain masked values without a provenance flag.
                snapshot["redacted"] = "[redacted]" in row["snapshot"]
            return 200, {**dict(row), "snapshot": snapshot}
        return None
