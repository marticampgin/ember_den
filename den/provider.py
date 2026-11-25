"""A streaming model provider contract and its loopback Ollama adapter."""

import http.client
import json
import re
import socket
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Protocol

from .cancel import Cancellation


class ProviderError(RuntimeError):
    pass


class Provider(Protocol):
    name: str

    def inventory(self) -> list[dict]: ...
    def embedding_inventory(self) -> list[dict]: ...
    def embed(self, model: str, texts: list[str], cancellation: Cancellation, device: str) -> list[list[float]]: ...

    def stream(self, messages: list[dict], config: dict, cancellation: Cancellation) -> Iterator[dict]:
        """Yield {'text': ...} chunks and a final {'metrics': ...} packet."""
        ...


def local_model(name, metadata):
    if metadata.get("remote_host") or metadata.get("remote_model"):
        return False
    details = metadata.get("details") or {}
    identifiers = f"{name} {details.get('family', '')}".lower()
    return not re.search(r"(?:^|[-_: /])cloud(?:$|[-_: /])", identifiers)


def embedding_name(name, metadata):
    return "embed" in (name + " " + str((metadata.get("details") or {}).get("family", ""))).lower()


def local_chat_model(name, metadata):
    return local_model(name, metadata) and not embedding_name(name, metadata)


def runtime_error(message, status):
    if not isinstance(message, str):
        return f"Ollama returned HTTP {status}."
    cause = message.casefold()
    if "out of memory" in cause or "requires more system memory" in cause:
        return "Ollama ran out of memory. Choose a smaller model or reduce the context length in Settings."
    if "cuda error" in cause and ("initialization" in cause or "terminated" in cause):
        return "Ollama's GPU runner failed to initialize this model. Try a smaller model in Settings or restart Ollama."
    return message[:600]


class VisibleContent:
    """Some GGUF templates put <think> blocks in content, even in non-thinking mode."""

    def __init__(self):
        self.pending = ""
        self.depth = 0

    def feed(self, text, final=False):
        data = self.pending + text
        self.pending = ""
        visible = []
        while data:
            head, marker, tail = data.partition("<")
            if not self.depth:
                visible.append(head)
            if not marker:
                break
            candidate = "<" + tail
            folded = candidate[:8].lower()
            if folded.startswith("<think>"):
                self.depth += 1
                data = tail[6:]
            elif folded == "</think>":
                self.depth = max(0, self.depth - 1)
                data = tail[7:]
            elif not final and ("<think>".startswith(folded) or "</think>".startswith(folded)):
                self.pending = candidate
                break
            else:
                if not self.depth:
                    visible.append("<")
                data = tail
        return "".join(visible)


class Ollama:
    name = "Ollama"

    @contextmanager
    def request(self, path, payload=None, timeout=10, cancellation=None):
        # HTTPConnection uses the literal loopback address and follows no redirects or proxies.
        connection = http.client.HTTPConnection("127.0.0.1", 11434, timeout=timeout)
        signal = cancellation or Cancellation()
        try:
            signal.check()
            body = json.dumps(payload).encode() if payload is not None else None
            connection.request("POST" if body else "GET", path, body,
                               {"Content-Type": "application/json"})
            sock = connection.sock

            def interrupt():
                try:
                    if sock:
                        sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

            with signal.interruptible(interrupt):
                with connection.getresponse() as response:
                    if response.status != 200:
                        detail = response.read(4096)
                        try:
                            message = json.loads(detail).get("error")
                        except (ValueError, AttributeError):
                            message = None
                        raise ProviderError(runtime_error(message, response.status))
                    yield response
        except TimeoutError as exc:
            raise ProviderError("Ollama timed out while preparing the response. Try a smaller model.") from exc
        except (OSError, http.client.HTTPException) as exc:
            signal.check()
            raise ProviderError("Could not reach local Ollama. Start Ollama and try again.") from exc
        except (ValueError, KeyError, TypeError) as exc:
            raise ProviderError("Ollama returned an unreadable response.") from exc
        finally:
            connection.close()

    def _available(self, embeddings):
        with self.request("/api/tags", timeout=2) as response:
            models = json.load(response)["models"]
            result = []
            for model in models:
                name = model["name"]
                if not local_model(name, model) or embedding_name(name, model) != embeddings:
                    continue
                details = model.get("details") or {}
                result.append({
                    "name": name, "digest": model.get("digest"), "size": model.get("size"),
                    "family": details.get("family"), "parameters": details.get("parameter_size"),
                    "quantization": details.get("quantization_level"),
                })
            return result

    def inventory(self):
        return self._available(False)

    def embedding_inventory(self):
        return self._available(True)

    def embed(self, model, texts, cancellation, device="auto"):
        with self.request("/api/show", {"model": model}, cancellation=cancellation) as response:
            info = json.load(response)
        if not local_model(model, info) or "embedding" not in (info.get("capabilities") or ["embedding"]):
            raise ProviderError("Choose an installed local embedding model.")
        payload = {"model": model, "input": texts, "truncate": True}
        if device == "cpu":
            payload["options"] = {"num_gpu": 0}
        with self.request("/api/embed", payload, timeout=180, cancellation=cancellation) as response:
            vectors = json.load(response)["embeddings"]
        if not isinstance(vectors, list) or len(vectors) != len(texts):
            raise ProviderError("Ollama returned an incomplete embedding batch.")
        return vectors

    def footprint(self, model):
        # Sample only this model's reported residency; this endpoint does not measure peak VRAM.
        try:
            with self.request("/api/ps", timeout=1) as response:
                loaded = json.load(response)["models"]
                item = next((r for r in loaded if r.get("name") == model or r.get("model") == model), {})
                return {"resident_vram_bytes": item.get("size_vram"), "resident_model_bytes": item.get("size"),
                        "effective_context_length": item.get("context_length")}
        except ProviderError:
            return {}

    def stream(self, messages, config, cancellation=None):
        model = config["model"]
        with self.request("/api/show", {"model": model}, cancellation=cancellation) as response:
            info = json.load(response)
        if not local_chat_model(model, info):
            raise ProviderError("Choose an installed local chat model in Settings.")
        capabilities = info.get("capabilities") or []
        if capabilities and "completion" not in capabilities:
            raise ProviderError("This model does not support chat. Choose a language model in Settings.")
        options = {"temperature": config["temperature"], "num_ctx": config["context_length"],
                   "num_predict": min(config.get("_max_output_tokens", 1024), config["context_length"] // 2)}
        if config.get("device") == "cpu":
            options["num_gpu"] = 0
        if config.get("seed") is not None:
            options["seed"] = config["seed"]
        payload = {"model": model, "messages": messages, "options": options, "stream": True}
        # Suppress separate thinking output on models advertising this capability.
        if "thinking" in capabilities:
            payload["think"] = False
        visible = VisibleContent()
        with self.request("/api/chat", payload, timeout=180, cancellation=cancellation) as response:
            for line in response:
                if cancellation:
                    cancellation.check()
                if not line.strip():
                    continue
                packet = json.loads(line)
                if packet.get("error"):
                    raise ProviderError(runtime_error(packet["error"], 500))
                raw_text = (packet.get("message") or {}).get("content") or ""
                token = visible.feed(raw_text, final=packet.get("done", False))
                if token:
                    yield {"text": token}
                if packet.get("done"):
                    elapsed = packet.get("eval_duration", 0)
                    count = packet.get("eval_count")
                    load = packet.get("load_duration")
                    yield {"metrics": {
                        **self.footprint(model),
                        "input_tokens": packet.get("prompt_eval_count"), "output_tokens": count,
                        "tokens_per_second": round(count * 1e9 / elapsed, 2)
                        if count is not None and elapsed else None,
                        "load_ms": round(load / 1e6, 2) if load is not None else None,
                        "finish_reason": packet.get("done_reason"),
                    }}
                    return
        if cancellation:
            cancellation.check()
        raise ProviderError("Ollama closed the response before completing it.")
