"""Experiment tool policies must remain reproducible and cannot execute approved actions."""

from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock

from den.engine import Engine, validate_config
from den.laboratory import Laboratory
from den.store import Store
from den.tools import Tool, Tools


class Model:
    name = "Fixture"

    def inventory(self):
        return [{"name": "fixture", "digest": "fixture-1"}]

    def stream(self, messages, config, cancellation):
        yield {"text": "Model response."}
        yield {"metrics": {"input_tokens": 3, "output_tokens": 3}}


class Source:
    def __init__(self):
        self.calls = 0

    def search(self, query):
        self.calls += 1
        return [
            {
                "name": "Fox",
                "id": "27",
                "kind": "web",
                "text": f"Version {self.calls} of {query}.",
                "url": "https://en.wikipedia.org/?curid=27",
            }
        ]


class PolicyTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.store = Store(self.path / "ember.sqlite3")
        self.public = Source()
        self.tools = Tools(self.path, self.public)
        self.engine = Engine(self.store, Model(), tools=self.tools)
        self.lab = Laboratory(
            self.store.path,
            self.engine.run_config,
            self.engine.provider.inventory,
            read_run=self.store.run,
        )

    def chat(self, prompt, mode):
        packets = list(
            self.engine.generate(prompt, {"model": "fixture", "tool_mode": mode})
        )
        return self.store.run(packets[0]["run_id"])

    def wait(self, id):
        for _ in range(400):
            result = self.lab.experiment(id)
            if result["status"] != "running":
                return result
            time.sleep(0.01)
        self.fail("Comparison timed out")

    def pair(self, prompt, mode_a="natural", mode_b="commands", **fields):
        return {
            "prompt": prompt,
            "configs": [{"model": "fixture", "tool_mode": m} for m in (mode_a, mode_b)],
            **fields,
        }

    def run_pair(self, payload):
        code, job = self.lab.route("POST", "/api/lab/experiments", payload)
        self.assertEqual(code, 202)
        return self.wait(job["id"])

    def test_daily_policy_controls_command_natural_and_calculator(self):
        self.assertEqual(
            self.chat("search Wikipedia for den", "natural")["route"], "wiki_search"
        )
        self.assertEqual(
            self.chat("search Wikipedia for den", "commands")["route"], "chat"
        )
        self.assertEqual(self.chat("/web den", "commands")["route"], "wiki_search")
        self.assertEqual(self.chat("/web den", "off")["route"], "chat")
        self.assertEqual(self.chat("calculate 2 + 3", "commands")["route"], "chat")
        self.assertEqual(self.chat("/calc 2+3", "commands")["response"], "5")
        self.assertEqual(self.chat("/calc 2+3", "off")["response"], "Model response.")
        self.assertEqual(self.public.calls, 2)
        with self.assertRaises(ValueError):
            validate_config({"tool_mode": "anything else"})

    def test_compare_modes_and_freeze_public_results_across_replay_and_catalog_changes(
        self,
    ):
        first = self.run_pair(self.pair("search Wikipedia for den"))
        a, b = [arm["run"] for arm in first["results"]]
        self.assertEqual((a["route"], b["route"]), ("wiki_search", "chat"))
        self.assertEqual(a["tool_execution"]["input"], {"query": "den"})
        self.assertEqual(a["tool_execution"]["output"]["response"], a["response"])
        original = a["tool_execution"]["catalog_revision"]
        self.tools.register(
            Tool(
                "extra",
                "Extra",
                "/extra",
                "/extra X",
                "public read",
                (("query", 10),),
                self.public.search,
            )
        )
        self.tools.registry.pop("wiki_search")
        _, rerun = self.lab.route(
            "POST", f"/api/lab/experiments/{first['id']}/replay", {}
        )
        result = self.wait(rerun["id"])
        capture = result["results"][0]["run"]["tool_execution"]
        self.assertEqual(capture["execution"], "replay")
        self.assertEqual(capture["original_catalog_revision"], original)
        self.assertNotEqual(capture["catalog_revision"], original)
        self.assertEqual(result["results"][0]["run"]["response"], a["response"])
        self.assertEqual(self.public.calls, 1)
        self.assertEqual(self.tools.state()["activity"], [])

    def test_share_identical_public_call_and_return_saved_case_result_after_source_deletion(
        self,
    ):
        first = self.run_pair(self.pair("/web den"))
        self.assertEqual(self.public.calls, 1)
        self.assertEqual(
            [a["run"]["tool_execution"]["execution"] for a in first["results"]],
            ["live read", "replay"],
        )
        captured = self.chat("search Wikipedia for den", "natural")
        payload = self.pair(captured["prompt"], source_run_id=captured["id"])
        _, case = self.lab.route(
            "POST", "/api/lab/cases", {**payload, "name": "Frozen read"}
        )
        self.store.delete_conversation(captured["conversation_id"])
        saved = self.lab.route("GET", "/api/lab/cases/" + case["id"])[1]["snapshot"]
        result = self.run_pair(saved)
        self.assertEqual(result["results"][0]["run"]["response"], captured["response"])
        self.assertEqual(result["results"][1]["run"]["route"], "chat")
        missing = self.run_pair(self.pair("/web other", source_run_id="nonexistent"))
        self.assertEqual(missing["status"], "failed")
        self.assertEqual(self.public.calls, 2)

    def test_side_effect_tools_always_preview_and_legacy_comparisons_run_models(self):
        handler = Mock(side_effect=AssertionError("Mutating handler executed in lab"))
        self.tools.register(
            Tool(
                "mutation",
                "Mutate",
                "/mutation",
                "/mutation item",
                "approval required",
                (("name", 25),),
                handler,
            )
        )
        for prompt in (
            "/event 2028-02-29 | Field trip",
            "/note Notes | A fox",
            "/mutation sample",
        ):
            first = self.run_pair(self.pair(prompt))
            self.assertEqual(first["status"], "completed")
            a, b = [arm["run"] for arm in first["results"]]
            self.assertEqual(a["response"], b["response"])
            self.assertIn("Laboratory preview", a["response"])
            self.assertEqual(a["tool_execution"]["execution"], "preview")
        handler.assert_not_called()
        self.assertFalse((self.path / "exports").exists())
        self.assertEqual(self.tools.state()["pending"], [])
        self.assertEqual(self.tools.state()["activity"], [])
        legacy = self.run_pair(
            {"prompt": "/web fox", "configs": [{"model": "fixture"}] * 2}
        )
        self.assertEqual(self.public.calls, 0)
        self.assertTrue(
            all(
                arm["run"]["tool_execution"]["mode"] == "off"
                for arm in legacy["results"]
            )
        )
