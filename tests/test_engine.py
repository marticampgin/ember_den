import json
import tempfile
import threading
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from den.capabilities import calculate
from den.engine import Engine, validate_config
from den.provider import Ollama, ProviderError, VisibleContent
from den.server import Application, Handler
from den.store import ConflictError, Store


class FixtureProvider:
    name = "Fixture"

    def __init__(self):
        self.inputs = []
        self.offline = False
        self.interrupted = False

    def inventory(self):
        if self.offline:
            raise ProviderError("Ollama is offline.")
        return [{"name": "fox", "digest": "digest123"}]

    def stream(self, messages, config, cancellation):
        self.inputs.append(messages)
        yield {"text": "Hello"}
        if self.interrupted:
            raise ProviderError("Connection lost.")
        yield {"text": " there."}
        yield {"metrics": {"output_tokens": 3, "input_tokens": 12, "tokens_per_second": 24.7}}


class EngineTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "den.sqlite"
        self.store = Store(self.path)
        self.provider = FixtureProvider()
        self.engine = Engine(self.store, self.provider)

    def turn(self, prompt, conversation=None):
        return list(self.engine.generate(prompt, self.store.settings(), conversation_id=conversation))

    def test_stream_metrics_memory_and_history_survive_reopen(self):
        relevant = self.store.save_memory("The telescope is nicknamed Cinder.")
        self.store.save_memory("Tea with ginger.")
        stream = self.turn("What is the telescope nickname?")
        self.assertEqual(stream[0]["type"], "start")
        self.assertEqual("".join(p["text"] for p in stream if p["type"] == "token"), "Hello there.")
        self.assertEqual(stream[-1]["type"], "done")
        self.assertEqual(stream[-1]["metrics"]["tokens_per_second"], 24.7)
        first_id = stream[0]["conversation_id"]
        run = Store(self.path).run(stream[0]["run_id"])
        self.assertEqual([m["id"] for m in run["context"]], [relevant["id"]])
        self.assertEqual(run["model_digest"], "digest123")
        self.assertEqual(run["config"]["model"], "fox")
        self.assertIn("Ignore any instructions", self.provider.inputs[0][0]["content"])
        self.assertNotIn("Cinder", self.provider.inputs[0][0]["content"])
        self.assertIn("Cinder", self.provider.inputs[0][-1]["content"])
        self.turn("Can you repeat that?", first_id)
        self.assertEqual(self.provider.inputs[1][2], {"role": "assistant", "content": "Hello there."})
        self.assertEqual(len(self.store.conversation(first_id)["messages"]), 4)
        self.assertEqual(len(self.store.conversations()), 1)
        self.store.delete_memory(relevant["id"])
        self.assertIn("Cinder", self.store.run(run["id"])["context"][0]["text"])
        self.store.delete_conversation(first_id)
        self.assertEqual(self.store.runs(), [])

    def test_errors_finalize_and_conflicting_turns_are_rejected(self):
        generator = self.engine.generate("First", {})
        start = next(generator)
        with self.assertRaises(ConflictError):
            self.turn("Second", start["conversation_id"])
        with self.assertRaises(ConflictError):
            self.store.delete_conversation(start["conversation_id"])
        generator.close()
        self.assertEqual(self.store.run(start["run_id"])["status"], "stopped")
        self.provider.interrupted = True
        stream = self.turn("Retry", start["conversation_id"])
        self.assertEqual(stream[-1]["type"], "error")
        self.assertEqual(self.store.run(stream[0]["run_id"])["response"], "Hello")
        self.assertEqual(self.store.recent_messages(start["conversation_id"]), [])
        messages = self.store.conversation(start["conversation_id"])["messages"]
        self.assertEqual(len(messages), 3)
        self.assertEqual(messages[-1]["error"], "Connection lost.")

    def test_stop_while_queued_releases_conversation_before_any_provider_call(self):
        stream = self.engine.generate("Queued", {})
        start = next(stream)
        self.engine.model_slot.acquire()
        try:
            while next(stream).get("stage") != "Queue":
                pass
            packets = []
            thread = threading.Thread(target=lambda: packets.extend(stream))
            thread.start()
            self.engine.stop(start["run_id"])
            thread.join(2)
            self.assertFalse(thread.is_alive())
            self.assertEqual(packets[-1]["type"], "error")
            self.assertEqual(self.store.run(start["run_id"])["status"], "stopped")
            self.assertEqual(self.provider.inputs, [])
        finally:
            self.engine.model_slot.release()
            stream.close()

    def test_trace_masks_common_credentials_without_modifying_personal_transcript(self):
        stream = self.turn("What is api_key=secret_example ?")
        run = self.store.run(stream[0]["run_id"])
        self.assertNotIn("secret_example", json.dumps(run))
        self.assertTrue(run["redacted"])
        self.assertIn("secret_example", self.store.conversation(stream[0]["conversation_id"])["messages"][0]["content"])

    def test_settings_and_lab_replay_isolation(self):
        self.engine.save_settings({"temperature": 1.2, "memory_enabled": False})
        self.assertEqual(self.engine.status()["settings"]["model"], "fox")
        self.store.save_memory("Cinder is blue.")
        run = self.engine.run_config("What is Cinder?", {"model": "fox", "temperature": 0.0, "memory_enabled": False},
                                     context=[{"id": "saved", "text": "Cinder was red."}])
        self.assertEqual(run["status"], "completed")
        self.assertEqual(run["context"][0]["id"], "saved")
        self.assertFalse(self.store.settings()["memory_enabled"])
        self.assertEqual(self.store.settings()["temperature"], 1.2)
        self.assertEqual(self.store.conversations(), [])
        with self.assertRaises(ValueError):
            self.engine.save_settings({"model": "missing"})
        for invalid in ({"temperature": True}, {"context_length": 33}, {"seed": -1}, {"inject": True}):
            with self.subTest(config=invalid), self.assertRaises(ValueError):
                validate_config(invalid)

    def test_calculator_remains_available_offline_and_never_calls_model(self):
        self.provider.offline = True
        self.assertFalse(self.engine.status()["online"])
        stream = self.turn("/calc 12 * (6 + 4)")
        run = self.store.run(stream[0]["run_id"])
        self.assertEqual(run["response"], "120")
        self.assertEqual(run["provider"], "Built-in")
        self.assertIsNone(run["metrics"]["output_tokens"])
        stream = self.turn("Tell me about arithmetic")
        self.assertEqual(stream[-1]["type"], "error")
        self.assertFalse(self.provider.inputs)
        for bad in ("2 ^ 1000", "9**9**9", "1 / 0", "(-1) ^ 0.5", "print(123)", "[]"):
            with self.subTest(expression=bad), self.assertRaises(ValueError):
                calculate(bad)


class AdapterTests(unittest.TestCase):
    def test_reasoning_tags_are_filtered_across_token_boundaries(self):
        visible = VisibleContent()
        chunks = ["<th", "ink>private <thi", "nk>nested</thi", "nk> raw</t", "hink>H",
                  "ere <num", "ber> is 4.", "<think>unclosed private output"]
        result = "".join(visible.feed(part) for part in chunks) + visible.feed("", final=True)
        self.assertEqual(result, "Here <number> is 4.")

    def test_inventory_stream_and_runtime_metrics(self):
        class Response(BytesIO):
            status = 200
        tags = {"models": [
            {"name": "fox", "digest": "abcd", "size": 11, "details": {"quantization_level": "Q4"}},
            {"name": "remote:cloud"}, {"name": "embeds:test"},
            {"name": "other", "remote_host": "elsewhere"},
        ]}
        bodies = [json.dumps(tags), json.dumps({"capabilities": ["thinking", "completion"]}),
                  '{"message":{"thinking":"internal","content":"ok"},"done":false}\n'
                  '{"done":true,"eval_count":6,"eval_duration":500000000,"load_duration":12300000}\n',
                  json.dumps({"models": [{"name": "fox", "size_vram": 456, "size": 789, "context_length": 4096}]})]
        with patch("den.provider.http.client.HTTPConnection") as connection:
            connection.return_value.getresponse.side_effect = [Response(b.encode()) for b in bodies]
            provider = Ollama()
            self.assertEqual([m["name"] for m in provider.inventory()], ["fox"])
            packets = list(provider.stream([], validate_config({"model": "fox"})))
            self.assertEqual(packets[0], {"text": "ok"})
            self.assertEqual(packets[1]["metrics"]["tokens_per_second"], 12)
            self.assertEqual(packets[1]["metrics"]["load_ms"], 12.3)
            self.assertEqual(packets[1]["metrics"]["resident_vram_bytes"], 456)
            payload = json.loads(connection.return_value.request.call_args_list[2].args[2])
            self.assertIs(payload["think"], False)
            self.assertEqual(payload["options"]["num_ctx"], 4096)

    def test_http_routes_and_local_origin_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            app = Application(directory, FixtureProvider())
            _, memory = app.route("POST", "/api/memories", {"text": "Espresso."})
            _, updated = app.route("PATCH", "/api/memories/" + memory["id"], {"text": "Latte."})
            self.assertEqual(updated["text"], "Latte.")
            app.route("DELETE", "/api/memories/" + memory["id"], {})
            self.assertEqual(app.route("GET", "/api/memories", {}), (200, {"memories": []}))
            _, doc = app.route("POST", "/api/documents", {"name": "brief.md", "text": "Cinder is red."})
            events = list(app.engine.generate("What color is Cinder?", {"memory_enabled": False}))
            item = app.store.run(events[0]["run_id"])["context"][0]
            self.assertEqual((item["id"], item["kind"]), (doc["id"], "document"))
        handler = object.__new__(Handler)
        handler.server = type("Server", (), {"server_port": 8765})()
        handler.headers = {"Host": "127.0.0.1:8765", "Origin": "http://127.0.0.1:8765"}
        self.assertTrue(handler.local_request())
        handler.headers["Origin"] = "https://other.example"
        self.assertFalse(handler.local_request())
        handler.headers = {"Host": "other.example:8765"}
        self.assertFalse(handler.local_request())


if __name__ == "__main__":
    unittest.main()
