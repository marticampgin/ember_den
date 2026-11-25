from contextlib import closing
import json
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path

from den.laboratory import Laboratory


class LaboratoryTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.db = Path(temp.name) / "lab.sqlite"
        self.calls = []
        self.models = [{"name": "fox:test", "digest": "abc123"}]
        self.lab = Laboratory(self.db, self.infer, lambda: self.models)
        self.input = {
            "prompt": "What is the sky color?", "expected": "BLUE",
            "configs": [{"model": "fox:test", "temperature": 0.1}, {"model": "fox:test", "temperature": 0.8}],
            "history": [{"role": "user", "content": "On a clear day."}],
            "context": [{"id": "note", "text": "Temperate weather."}],
        }

    def infer(self, prompt, config, *, context, history):
        self.calls.append((prompt, config.copy(), context.copy(), history.copy()))
        return {"id": str(len(self.calls)), "status": "completed", "error": None,
                "response": "Blue." if config["temperature"] < 0.5 else "Sapphire.",
                "metrics": {"ttft_ms": 17}, "config": config, "context": context, "history": history}

    def finish(self, identifier):
        for _ in range(300):
            out = self.lab.experiment(identifier)
            if out["status"] != "running":
                return out
            time.sleep(0.01)
        self.fail("comparison did not finish")

    def start(self):
        status, result = self.lab.route("POST", "/api/lab/experiments", self.input)
        self.assertEqual(status, 202)
        return result["id"]

    def test_both_arms_get_identical_context_and_separate_expected_checks(self):
        first = self.finish(self.start())
        self.assertEqual(first["status"], "completed")
        self.assertEqual([r["check"] for r in first["results"]], [True, False])
        self.assertEqual([c[1]["temperature"] for c in self.calls], [0.1, 0.8])
        self.assertEqual(self.calls[0][2:], self.calls[1][2:])
        self.assertEqual(first["snapshot"]["models"][0]["digest"], "abc123")
        self.assertEqual(first["results"][0]["run"]["metrics"], {"ttft_ms": 17})
        self.assertTrue(first["snapshot"]["configs"][0]["system_prompt"])
        path = f"/api/lab/experiments/{first['id']}/replay"
        _, replay = self.lab.route("POST", path, {})
        second = self.finish(replay["id"])
        self.assertEqual(second["replay_of"], first["id"])
        self.assertEqual(second["snapshot"], first["snapshot"])
        reopened = Laboratory(self.db, self.infer, lambda: [])
        self.assertEqual(len(reopened.route("GET", "/api/lab/experiments")[1]["experiments"]), 2)

    def test_concurrent_submission_rejected_and_partial_failures_preserved(self):
        reached = threading.Event()
        release = threading.Event()
        def unstable(prompt, config, **kwargs):
            if config["temperature"] < 0.5:
                reached.set()
                release.wait(2)
                raise RuntimeError("Provider failed")
            return self.infer(prompt, config, **kwargs)
        self.lab.run_config = unstable
        identifier = self.start()
        try:
            self.assertTrue(reached.wait(2))
            self.assertEqual(self.lab.route("POST", "/api/lab/experiments", self.input)[0], 409)
        finally:
            release.set()
        result = self.finish(identifier)
        self.assertEqual(result["status"], "partial")
        self.assertIsNone(result["results"][0]["check"])
        self.assertEqual(result["results"][0]["error"], "Provider failed")
        self.assertEqual(result["results"][1]["status"], "completed")

    def test_case_roundtrip_and_sensitive_inputs_are_redacted(self):
        self.input["prompt"] = "Explain API_KEY=abcdef12345"
        status, case = self.lab.route("POST", "/api/lab/cases", {**self.input, "name": "Credential check"})
        self.assertEqual(status, 201)
        path = f"/api/lab/cases/{case['id']}"
        saved = self.lab.route("GET", path)[1]["snapshot"]
        self.assertIn("[redacted]", saved["prompt"])
        self.assertTrue(saved["redacted"])
        self.assertIsNone(saved["configs"][0]["seed"])
        _, case_run = self.lab.route("POST", "/api/lab/experiments", saved)
        case_result = self.finish(case_run["id"])
        self.assertTrue(case_result["snapshot"]["source_redacted"])
        self.assertIsNone(case_result["results"][0]["run"]["config"]["seed"])
        # Older saved cases had the mask in their text but no explicit provenance field.
        del saved["redacted"]
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute("UPDATE lab_cases SET snapshot=? WHERE id=?", (json.dumps(saved), case["id"]))
        self.assertTrue(self.lab.route("GET", path)[1]["snapshot"]["redacted"])
        comparison = self.finish(self.start())
        self.assertTrue(comparison["snapshot"]["redacted"])
        self.assertNotIn("abcdef12345", str(comparison))
        self.models[0]["digest"] = "new-revision"
        _, rerun = self.lab.route("POST", f"/api/lab/experiments/{comparison['id']}/replay", {})
        second = self.finish(rerun["id"])
        self.assertEqual(second["snapshot"]["changed_models"], ["A", "B"])
        self.assertTrue(second["snapshot"]["source_redacted"])
        self.lab.route("DELETE", path)
        with self.assertRaises(KeyError):
            self.lab.route("GET", path)

    def test_reject_invalid_snapshot_and_unknown_model_before_queueing(self):
        with self.assertRaises(ValueError):
            self.lab.route("POST", "/api/lab/experiments", {**self.input, "history": [{"role":"system","content":"bad"}]})
        self.models.clear()
        self.assertEqual(self.lab.route("POST", "/api/lab/experiments", self.input)[0], 400)
        self.assertEqual(self.lab.route("GET", "/api/lab/experiments")[1]["experiments"], [])
        self.assertEqual(self.calls, [])
        self.models.append({"name": "fox:test"})
        self.assertEqual(self.finish(self.start())["status"], "completed")


if __name__ == "__main__":
    unittest.main()
