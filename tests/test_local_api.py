import io
import json
import unittest
from unittest.mock import MagicMock, patch

from den.cancel import Cancellation, Stopped
from den.local_api import LocalRuntimes, OpenAICompatible, make_provider, model_key
from den.provider import ProviderError


def reply(body, status=200):
    obj = io.BytesIO(body if isinstance(body, bytes) else json.dumps(body).encode())
    obj.status = status
    return obj


class Wire:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []
        self.destinations = []

    def __call__(self, host, port, timeout):
        self.destinations.append((host, port))
        connection = MagicMock()
        connection.request.side_effect = lambda method, path, body, headers: (
            self.calls.append((method, path, json.loads(body) if body else None))
        )
        connection.getresponse.side_effect = lambda: self.responses.pop(0)
        return connection


class CompatibleAPITest(unittest.TestCase):
    def setUp(self):
        self.provider = OpenAICompatible(12456)
        self.models = {
            "data": [
                {"id": "fox", "meta": {"size": 100}},
                {"id": "some-embedding"},
                {"id": "fox:cloud"},
                {"id": "pretend-local", "remote_host": "remote.invalid"},
                {"id": "remote-other", "type": "cloud"},
            ]
        }
        self.config = {
            "model": "local-api/fox",
            "temperature": 0.3,
            "context_length": 2048,
            "seed": None,
        }

    def test_sse_stream_selection_and_usage(self):
        stream = (
            b": ping\r\n\r\n"
            b'data: {"choices":[{"index":0,"delta":{"content":"<thi"}}]}\r\n\r\n'
            b'data: {"choices":[{"index":0,"delta":{"content":"nk>private</think>Blue"}}]}\n\n'
            b'data: {"choices":[{"delta":{}, "finish_reason":"stop"}],\n'
            b'data: "timings":{"predicted_per_second":22.5}}\n\n'
            b'data: {"choices":[],"usage":{"prompt_tokens":12,"completion_tokens":4}}\n\n'
            b"data: [DONE]\n\n"
        )
        wire = Wire(reply(self.models), reply(stream))
        with patch("den.local_api.http.client.HTTPConnection", wire):
            self.assertEqual(
                [m["name"] for m in self.provider.inventory()], ["local-api/fox"]
            )
            self.assertEqual(
                self.provider.embedding_inventory()[0]["name"],
                "local-api/some-embedding",
            )
            tokens = list(
                self.provider.stream(
                    [{"role": "user", "content": "Hi"}],
                    {**self.config, "_max_output_tokens": 12},
                )
            )
        self.assertEqual(tokens[0], {"text": "Blue"})
        self.assertEqual(
            tokens[1]["metrics"],
            {
                "tokens_per_second": 22.5,
                "input_tokens": 12,
                "output_tokens": 4,
                "finish_reason": "stop",
            },
        )
        method, path, body = wire.calls[1]
        self.assertEqual((method, path), ("POST", "/v1/chat/completions"))
        self.assertEqual(body["model"], "fox")
        self.assertEqual(body["max_tokens"], 12)
        self.assertNotIn("seed", body)
        self.assertEqual(set(wire.destinations), {("127.0.0.1", 12456)})

    def test_embedding_order_remote_filter_and_limits(self):
        wire = Wire(
            reply(self.models),
            reply(
                {
                    "data": [
                        {"index": 1, "embedding": [0, 1]},
                        {"index": 0, "embedding": [1, 0]},
                    ]
                }
            ),
        )
        with patch("den.local_api.http.client.HTTPConnection", wire):
            self.assertEqual(
                self.provider.embed(
                    "local-api/some-embedding", ["one", "two"], Cancellation()
                ),
                [[1, 0], [0, 1]],
            )
            for identifier in ("unknown", "fox:cloud", "some-embedding"):
                with self.assertRaises(ProviderError):
                    list(
                        self.provider.stream(
                            [], {**self.config, "model": model_key(identifier)}
                        )
                    )
            with self.assertRaisesRegex(ProviderError, "hardware placement"):
                list(self.provider.stream([], {**self.config, "device": "cpu"}))
        self.assertEqual(wire.calls[-1][1], "/v1/embeddings")
        for port in (-1, 0, 65536, "127.0.0.1", False):
            with self.assertRaises(ValueError):
                make_provider("auto", port)
        key = model_key("model name @ q4")
        self.assertRegex(key, r"^local-api/[\w.-]+$")

    def test_unfinished_stream_and_preexisting_cancellation(self):
        wire = Wire(
            reply(self.models),
            reply(b'data: {"choices":[{"delta":{"content":"part"}}]}\n\n'),
        )
        with patch("den.local_api.http.client.HTTPConnection", wire):
            with self.assertRaisesRegex(ProviderError, "unfinished"):
                list(self.provider.stream([], self.config))
            cancelled = Cancellation()
            cancelled.stop("Stop requested.")
            with self.assertRaises(Stopped):
                list(self.provider.stream([], self.config, cancelled))

    def test_auto_keeps_native_online_when_optional_probe_fails(self):
        native = MagicMock()
        native.name = "Ollama"
        native.inventory.return_value = [{"name": "fox"}]
        native.embedding_inventory.return_value = []
        mux = LocalRuntimes(native, self.provider)
        connection = MagicMock()
        connection.connect.side_effect = ConnectionRefusedError
        with patch(
            "den.local_api.http.client.HTTPConnection", return_value=connection
        ) as connect:
            self.assertEqual(mux.inventory(), [{"name": "fox", "provider": "Ollama"}])
            self.assertEqual(mux.embedding_inventory(), [])
            self.assertEqual(mux.inventory()[0]["name"], "fox")
            self.assertEqual(connect.call_count, 1)
        self.assertEqual(mux.label_for("local-api/fox"), self.provider.name)
        self.assertEqual(mux.label_for("fox"), "Ollama")
