"""A loopback OpenAI-compatible streaming transport for llama.cpp and LM Studio."""

from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
import http.client
import json
import math
import re
import socket
import threading
import time

from .cancel import Cancellation
from .provider import Ollama, ProviderError, VisibleContent, embedding_name, local_model

PREFIX = "local-api/"


def port_number(value):
    if type(value) is not int or not 1 <= value <= 65535:
        raise ValueError("The compatible API port must be an integer from 1 to 65535.")
    return value


def model_key(raw_id):
    if len(raw_id) <= 222 and re.fullmatch(r"[\w.:/-]+", raw_id):
        return PREFIX + raw_id
    slug = re.sub(r"[^\w.:/-]", "-", raw_id)[:180].strip("-") or "model"
    return PREFIX + slug + "-" + sha256(raw_id.encode()).hexdigest()[:12]


def events(response):
    """Decode complete SSE data frames, retaining a size bound across multi-line events."""
    parts, size = [], 0
    while True:
        line = response.readline(1048577)
        if len(line) > 1048576:
            raise ProviderError(
                "The compatible server returned an oversized stream line."
            )
        if not line or line in (b"\n", b"\r\n"):
            if parts:
                yield b"\n".join(parts).decode("utf-8")
                parts, size = [], 0
            if not line:
                return
        elif line.startswith(b"data:"):
            piece = line[5:].lstrip(b" ").rstrip(b"\r\n")
            size += len(piece)
            if size > 1048576:
                raise ProviderError(
                    "The compatible server returned an oversized stream event."
                )
            parts.append(piece)


class OpenAICompatible:
    def __init__(self, port=8080):
        self.port = port_number(port)
        self.name = f"Local compatible API (127.0.0.1:{self.port})"
        self._lock = threading.Lock()
        self._catalog = None
        self._error = None
        self._expires = 0

    @contextmanager
    def _request(self, path, payload=None, *, signal=None, timeout=15):
        signal = signal or Cancellation()
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=0.45)
        try:
            signal.check()
            conn.connect()
            sock = conn.sock
            sock.settimeout(timeout)

            def stop():
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

            with signal.interruptible(stop):
                body = (
                    None
                    if payload is None
                    else json.dumps(payload, allow_nan=False).encode("utf-8")
                )
                conn.request(
                    "GET" if body is None else "POST",
                    path,
                    body,
                    {
                        "Content-Type": "application/json",
                        "Accept": "text/event-stream, application/json",
                    },
                )
                with conn.getresponse() as response:
                    if response.status != 200:
                        raw = response.read(4096)
                        try:
                            error = json.loads(raw).get("error")
                            message = (
                                error.get("message")
                                if isinstance(error, dict)
                                else error
                            )
                        except (ValueError, AttributeError):
                            message = ""
                        suffix = (
                            ": " + message[:450]
                            if isinstance(message, str) and message
                            else ""
                        )
                        raise ProviderError(
                            f"Local compatible API returned HTTP {response.status}{suffix}"
                        )
                    yield response
        except (OSError, http.client.HTTPException) as exc:
            signal.check()
            raise ProviderError(
                f"Could not reach the local compatible API on port {self.port}."
            ) from exc
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise ProviderError(
                "The local compatible API returned an unreadable response."
            ) from exc
        finally:
            conn.close()

    @staticmethod
    def _json(response, limit):
        data = response.read(limit + 1)
        if len(data) > limit:
            raise ProviderError(
                "The local compatible API returned an oversized response."
            )
        return json.loads(data)

    def _models(self):
        # Several stages ask for inventory per turn; avoid reconnecting to an absent optional service.
        with self._lock:
            if time.monotonic() < self._expires:
                if self._error:
                    raise ProviderError(self._error)
                return self._catalog
            try:
                with self._request("/v1/models", timeout=3) as response:
                    rows = self._json(response, 4194304)["data"]
                    if not isinstance(rows, list):
                        raise ProviderError("The local model inventory is invalid.")
                catalog = {}
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    raw = row.get("id")
                    if (
                        not isinstance(raw, str)
                        or not raw
                        or len(raw) > 1024
                        or not local_model(raw, row)
                        or str(row.get("type", "")).lower() in {"remote", "cloud"}
                    ):
                        continue
                    meta = row.get("meta") or {}
                    kind = str(row.get("type") or "").lower()
                    is_embedding = kind == "embedding" or embedding_name(raw, row)
                    key = model_key(raw)
                    info = {
                        "name": key,
                        "display_name": raw,
                        "provider": self.name,
                        "runtime_port": self.port,
                        "digest": row.get("digest"),
                        "size": meta.get("size"),
                        "family": meta.get("architecture"),
                        "parameters": meta.get("n_params"),
                        "quantization": meta.get("quantization"),
                    }
                    catalog[key] = (raw, is_embedding, info)
                self._catalog = catalog
                self._error = None
            except ProviderError as exc:
                self._error = str(exc)
                raise
            finally:
                self._expires = time.monotonic() + (15 if self._error else 2)
            return self._catalog

    def inventory(self):
        return [
            dict(info)
            for _, embedding, info in self._models().values()
            if not embedding
        ]

    def embedding_inventory(self):
        return [
            dict(info) for _, embedding, info in self._models().values() if embedding
        ]

    def _select(self, model, *, embedding, device):
        if device == "cpu":
            raise ProviderError(
                "The compatible server controls hardware placement. Use Auto in Ember and configure CPU mode in llama.cpp or LM Studio."
            )
        found = self._models().get(model)
        if not found or found[1] != embedding:
            raise ProviderError("Select a model listed by the local compatible server.")
        return found[0]

    def embed(self, model, texts, cancellation, device="auto"):
        raw = self._select(model, embedding=True, device=device)
        with self._request(
            "/v1/embeddings",
            {"model": raw, "input": texts},
            signal=cancellation,
            timeout=180,
        ) as response:
            rows = self._json(response, 64 * 1024 * 1024)["data"]
            if not isinstance(rows, list) or len(rows) != len(texts):
                raise ProviderError("Incomplete local embedding response.")
            rows = sorted(rows, key=lambda row: row["index"])
            if any(
                row.get("index") != index or not isinstance(row.get("embedding"), list)
                for index, row in enumerate(rows)
            ):
                raise ProviderError("Invalid local embedding response.")
            return [row["embedding"] for row in rows]

    def stream(self, messages, config, cancellation=None):
        signal = cancellation or Cancellation()
        raw = self._select(
            config["model"], embedding=False, device=config.get("device", "auto")
        )
        payload = {
            "model": raw,
            "messages": messages,
            "temperature": config["temperature"],
            "stream": True,
            "stream_options": {"include_usage": True},
            "max_tokens": min(
                config.get("_max_output_tokens", 1024), config["context_length"] // 2
            ),
        }
        if config.get("seed") is not None:
            payload["seed"] = config["seed"]
        visible = VisibleContent()
        metrics = {}
        finished = False
        with self._request(
            "/v1/chat/completions", payload, signal=signal, timeout=180
        ) as response:
            for data in events(response):
                signal.check()
                if data.strip() == "[DONE]":
                    finished = True
                    break
                packet = json.loads(data)
                if packet.get("error"):
                    raise ProviderError(
                        "The compatible server reported an error during generation."
                    )
                usage = packet.get("usage") or {}
                for field, key in (
                    ("prompt_tokens", "input_tokens"),
                    ("completion_tokens", "output_tokens"),
                ):
                    value = usage.get(field)
                    if type(value) is int and value >= 0:
                        metrics[key] = value
                rate = (packet.get("timings") or {}).get("predicted_per_second")
                if type(rate) in (float, int) and math.isfinite(rate) and rate >= 0:
                    metrics["tokens_per_second"] = round(rate, 2)
                for choice in packet.get("choices") or []:
                    if choice.get("index", 0) != 0:
                        continue
                    content = (choice.get("delta") or {}).get("content") or ""
                    if isinstance(content, list):
                        content = "".join(
                            part["text"]
                            for part in content
                            if part.get("type") == "text"
                        )
                    chunk = visible.feed(content)
                    if chunk:
                        yield {"text": chunk}
                    if choice.get("finish_reason") is not None:
                        finished = True
                        metrics["finish_reason"] = choice["finish_reason"]
        signal.check()
        if not finished:
            raise ProviderError("The compatible server closed an unfinished stream.")
        tail = visible.feed("", final=True)
        if tail:
            yield {"text": tail}
        yield {"metrics": metrics}


class LocalRuntimes:
    """Model IDs select one transport throughout chat, retrieval and laboratory executions."""

    name = "Local runtimes"

    def __init__(self, native, compatible):
        self.native = native
        self.compatible = compatible

    def _provider(self, model):
        return self.compatible if model.startswith(PREFIX) else self.native

    def label_for(self, model):
        return self._provider(model).name

    def _available(self, method):
        rows, errors = [], []
        for provider in (self.native, self.compatible):
            try:
                available = getattr(provider, method)()
                rows.extend({**row, "provider": provider.name} for row in available)
            except ProviderError as exc:
                errors.append(str(exc))
        if len(errors) == 2:
            raise ProviderError(" ".join(errors))
        return rows

    def inventory(self):
        return self._available("inventory")

    def embedding_inventory(self):
        return self._available("embedding_inventory")

    def embed(self, model, texts, cancellation, device="auto"):
        return self._provider(model).embed(model, texts, cancellation, device)

    def stream(self, messages, config, cancellation=None):
        yield from self._provider(config["model"]).stream(
            messages, config, cancellation
        )


def make_provider(mode="auto", port=8080):
    port_number(port)
    if mode == "ollama":
        return Ollama()
    if mode == "openai-compatible":
        return OpenAICompatible(port)
    if mode == "auto":
        return LocalRuntimes(Ollama(), OpenAICompatible(port))
    raise ValueError("Unknown local provider.")
