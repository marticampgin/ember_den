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

    def stream(self, messages: list[dict], config: dict, cancellation: Cancellation) -> Iterator[dict]:
        """Yield {'text': ...} chunks and a final {'metrics': ...} packet."""
        ...


def local_chat_model(name, metadata):
    if metadata.get("remote_host") or metadata.get("remote_model"):
        return False
    details = metadata.get("details") or {}
    identifiers = f"{name} {details.get('family', '')}".lower()
    return not re.search(r"embed|(?:^|[-_: /])cloud(?:$|[-_: /])", identifiers)


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
                        raise ProviderError(message or f"Ollama returned HTTP {response.status}.")
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

    def inventory(self):
        with self.request("/api/tags", timeout=2) as response:
            models = json.load(response)["models"]
            result = []
            for model in models:
                name = model["name"]
                if not local_chat_model(name, model):
                    continue
                details = model.get("details") or {}
                result.append({
                    "name": name, "digest": model.get("digest"), "size": model.get("size"),
                    "family": details.get("family"), "parameters": details.get("parameter_size"),
                    "quantization": details.get("quantization_level"),
                })
            return result

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
                   "num_predict": min(1024, config["context_length"] // 2)}
        if config.get("seed") is not None:
            options["seed"] = config["seed"]
        payload = {"model": model, "messages": messages, "options": options, "stream": True}
        # Suppress separate thinking output on models advertising this capability.
        if "thinking" in capabilities:
            payload["think"] = False
        with self.request("/api/chat", payload, timeout=180, cancellation=cancellation) as response:
            for line in response:
                if cancellation:
                    cancellation.check()
                if not line.strip():
                    continue
                packet = json.loads(line)
                if packet.get("error"):
                    raise ProviderError(str(packet["error"]))
                token = (packet.get("message") or {}).get("content")
                if token:
                    yield {"text": token}
                if packet.get("done"):
                    elapsed = packet.get("eval_duration", 0)
                    count = packet.get("eval_count")
                    load = packet.get("load_duration")
                    yield {"metrics": {
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
