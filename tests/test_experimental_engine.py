import tempfile
import unittest
from pathlib import Path

from den.cancel import Cancellation
from den.documents import Documents
from den.engine import Engine, validate_config
from den.provider import ProviderError
from den.store import Store


class TestProvider:
    name = "Fixture"

    def __init__(self):
        self.models = ["author", "specialist", "reviewer"]
        self.model_error = ""
        self.embed_batches = []
        self.queries = []
        self.category = "SPECIALIST"

    def inventory(self):
        return [{"name": name, "digest": name + "-rev"} for name in self.models]

    def embedding_inventory(self):
        return [{"name": "embed", "digest": "embed-v1"}]

    def embed(self, model, texts, cancellation, device):
        self.embed_batches.append(texts)
        return [[1., 0.] if "spectacles" in s or "glasses" in s else [0., 1.] for s in texts]

    def stream(self, messages, config, cancellation):
        name = config["model"]
        self.queries.append((name, messages))
        if self.model_error == name:
            raise ProviderError("Simulated load failure")
        yield {"text": self.category if "_max_output_tokens" in config else f"Visible {name} answer"}
        yield {"metrics": {"input_tokens": 10, "output_tokens": 3}}


class ExperimentalEngineTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "db.sqlite"
        self.store = Store(path)
        self.docs = Documents(path)
        self.provider = TestProvider()
        self.engine = Engine(self.store, self.provider, self.docs)

    def chat(self, prompt, config=None, conversation=None):
        events = list(self.engine.generate(prompt, {"model": "author", **(config or {})}, conversation_id=conversation))
        return self.store.run(events[0]["run_id"])

    def test_semantic_cache_revision_frozen_replay_and_deletion(self):
        self.docs.add("weather.txt", "It is raining.")
        target = self.docs.add("packing.txt", "Pack the spare spectacles.")
        config = {"model": "author", "retrieval_mode": "semantic"}
        first = self.engine.run_config("Find my glasses", config)
        self.assertEqual(first["context"][0]["name"], "packing.txt")
        self.assertEqual(first["retrieval"]["new_vectors"], 2)
        second = self.engine.run_config("Find my glasses", {**config, "retrieval_mode": "hybrid"})
        self.assertEqual(second["retrieval"]["new_vectors"], 0)
        _, meta = self.docs.ranked("glasses", "semantic", self.provider,
                                   {"name": "embed", "digest": "embed-v2"}, Cancellation(), "auto")
        self.assertEqual(meta["new_vectors"], 2)
        self.docs.delete(target["id"])
        before = len(self.provider.embed_batches)
        replay = self.engine.run_config("Find my glasses", config, context=first["context"])
        self.assertEqual(replay["context"], first["context"])
        self.assertEqual(len(self.provider.embed_batches), before)
        self.assertEqual(self.engine.run_config("Find my glasses", config)["context"], [])
        with self.store.connect() as db:
            self.assertFalse(db.execute("SELECT 1 FROM document_vectors WHERE id=?", (target["id"],)).fetchone())

    def test_fallback_to_keyword_and_missing_specialist(self):
        self.docs.add("notes.txt", "Beryl is blue")
        run = self.engine.run_config("Compare Beryl with another stone.", {
            "model":"author", "retrieval_mode":"semantic", "embedding_model":"unavailable",
            "routing_mode":"adaptive", "specialist_model":"missing",
        })
        self.assertEqual(run["status"], "completed")
        self.assertEqual(run["retrieval"]["mode"], "keyword")
        self.assertEqual(run["model"], "author")
        self.assertEqual(run["context"][0]["name"], "notes.txt")
        self.assertTrue(any(e["stage"] == "Fallback" for e in run["events"]))
        self.assertIn("no available specialist", next(e["detail"] for e in run["events"] if e["stage"]=="Model selection"))

    def test_adaptive_revision_streams_only_final_and_records_costs(self):
        config = {"model":"author", "routing_mode":"adaptive", "specialist_model":"specialist",
                  "workflow":"revise", "reviewer_model":"reviewer"}
        result = self.chat("Compare two approaches.", config)
        self.assertEqual(result["response"], "Visible reviewer answer")
        self.assertEqual(result["model"], "reviewer")
        self.assertEqual([p["model"] for p in result["workflow_passes"]], ["specialist", "reviewer"])
        self.assertIn("Visible specialist answer", self.provider.queries[-1][1][-1]["content"])
        self.assertEqual(result["metrics"]["pipeline_output_tokens"], 6)
        self.provider.model_error = "reviewer"
        fallback = self.engine.run_config("Compare methods.", config)
        self.assertEqual(fallback["response"], "Visible specialist answer")
        self.assertEqual(fallback["status"], "completed")
        self.assertEqual(fallback["model"], "specialist")
        self.assertEqual([p["status"] for p in fallback["workflow_passes"]], ["completed","error"])

    def test_episodes_skip_current_conversation_and_disabled_memory_and_respect_deletion(self):
        source = self.chat("My telescope is called Cinder.")
        options = {"memory_strategy": "episodes"}
        self.assertFalse(self.chat("Cinder?", options, source["conversation_id"])["context"])
        recalled = self.chat("Cinder?", options)
        episode = next(r for r in recalled["context"] if r["run_id"] == source["id"])
        self.assertEqual(episode["kind"], "episode")
        self.assertEqual(episode["conversation_id"], source["conversation_id"])
        self.assertFalse(self.chat("Cinder?", {**options, "memory_enabled":False})["context"])
        self.store.delete_conversation(source["conversation_id"])
        future = self.engine.run_config("Cinder?", options)
        self.assertNotIn(source["id"], [r.get("run_id") for r in future["context"]])
        replay = self.engine.run_config("Cinder?", options, context=[episode])
        self.assertEqual(replay["context"], [episode])

    def test_router_model_selection_and_invalid_classifier_fallback(self):
        cfg = {"routing_mode":"model", "router_model":"reviewer", "specialist_model":"specialist"}
        landed = self.chat("Question?", cfg)
        self.assertEqual(landed["model"], "specialist")
        router = landed["workflow_passes"][0]
        self.assertEqual((router["role"],router["digest"],router["max_output_tokens"]),
                         ("router","reviewer-rev",12))
        self.assertEqual(router["classification"], "SPECIALIST")
        self.provider.category = "Here is what I think instead"
        bad = self.chat("Question?", cfg)
        self.assertEqual(bad["model"], "author")
        self.assertEqual(bad["workflow_passes"][0]["status"], "error")
        self.assertNotIn(self.provider.category, str(bad))
        self.assertTrue(any(e["stage"] == "Fallback" for e in bad["events"]))

    def test_invalid_workflow_and_device_are_rejected_before_chat(self):
        for config in ({"workflow": "forever"}, {"device": "cloud"}, {"routing_mode": False},
                       {"retrieval_mode": "bogus"}, {"memory_strategy": "all"}, {"reviewer_model":"http://remote host"}):
            with self.subTest(config=config), self.assertRaises(ValueError):
                validate_config(config)
