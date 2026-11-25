"""Run with python -m den.server. Serves the app and API on loopback only."""

import argparse
import json
import mimetypes
import os
import select
import socket
import sys
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from itertools import chain
from urllib.parse import unquote, urlsplit

from .documents import Documents
from .importer import extract_document
from .insights import overview
from .cancel import Cancellation
from .engine import Engine, checked_text
from .laboratory import Laboratory
from .provider import Ollama, ProviderError
from .store import ConflictError, Store
from .tools import Tools
from .voice import Voice


WEB_ROOT = Path(__file__).resolve().parent.parent / "web"
MAX_BODY = 1024 * 1024


class Application:
    def __init__(self, data_dir, provider=None):
        self.store = Store(Path(data_dir) / "ember.sqlite3")
        self.store.recover()
        self.documents = Documents(self.store.path)
        self.tools = Tools(data_dir)
        self.voice = Voice()
        self.engine = Engine(self.store, provider or Ollama(), self.documents, tools=self.tools)
        self.lab = Laboratory(self.store.path, self.engine.run_config, self.engine.provider.inventory)

    def route(self, method, path, body):
        if path == "/api/lab/overview" and method == "GET":
            return 200, overview(self.store)
        if path == "/api/documents/import" and method == "POST":
            name, text = extract_document(body)
            return 201, self.documents.add(name,text)
        if path == "/api/tools" or path.startswith("/api/tools/"):
            return self.tools.route(method,path,body)
        if path.startswith("/api/voice/"):
            return self.voice.route(method,path,body)
        if path.startswith("/api/lab/"):
            return self.lab.route(method, path, body)
        if path == "/api/status" and method == "GET":
            return 200, self.engine.status()
        if path == "/api/settings":
            if method == "GET":
                return 200, self.engine.status()["settings"]
            if method == "PATCH":
                return 200, self.engine.save_settings(body)
        parts = path.strip("/").split("/")
        if len(parts)==4 and parts[1]=="runs" and parts[3]=="feedback" and method in ("GET","POST"):
            return 200, self.store.feedback(parts[2],body if method=="POST" else None)
        if len(parts) == 4 and parts[1] == "runs" and parts[3] == "stop" and method == "POST":
            self.engine.stop(parts[2])
            return 200, {"ok": True}
        if len(parts) not in (2, 3):
            return None
        resource, item = parts[1], parts[2] if len(parts) == 3 else None
        if resource == "conversations":
            if item and method == "DELETE":
                self.store.delete_conversation(item)
                return 200, {"ok": True}
            if method == "GET":
                return 200, self.store.conversation(item) if item else {"conversations": self.store.conversations()}
        elif resource == "runs" and method == "GET":
            return 200, self.store.run(item) if item else {"runs": self.store.runs()}
        elif resource == "memories":
            if method == "GET" and not item:
                return 200, {"memories": self.store.memories()}
            if (method == "POST" and not item) or (method == "PATCH" and item):
                text = checked_text(body.get("text"), "Memory", 2000)
                return (201 if not item else 200), self.store.save_memory(text, item)
            if method == "DELETE" and item:
                self.store.delete_memory(item)
                return 200, {"ok": True}
        elif resource == "documents":
            if method == "GET":
                return 200, self.documents.get(item) if item else {"documents": self.documents.list()}
            if method == "POST" and not item:
                return 201, self.documents.add(body.get("name"), body.get("text"))
            if method == "DELETE" and item:
                self.documents.delete(item)
                return 200, {"ok": True}
        return None


class Handler(BaseHTTPRequestHandler):
    server_version = "EmberDen"
    sys_version = ""

    def log_request(self, code="-", size="-"):
        # Keep prompts, document search queries and conversation IDs out of access logs.
        pass

    def headers_for(self, code, content_type, length=None):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                         "img-src 'self' data: blob:; connect-src 'self'; frame-ancestors 'none'; "
                         "object-src 'none'; base-uri 'none'; form-action 'self'")
        if length is not None:
            self.send_header("Content-Length", str(length))
        self.end_headers()

    def json(self, code, value):
        data = json.dumps(value, ensure_ascii=False).encode()
        self.headers_for(code, "application/json; charset=utf-8", len(data))
        if self.command != "HEAD":
            self.wfile.write(data)

    def local_request(self):
        port = self.server.server_port
        hosts = {f"localhost:{port}", f"127.0.0.1:{port}"}
        if port == 80:
            hosts |= {"localhost", "127.0.0.1"}
        host = self.headers.get("Host", "").lower()
        origin = self.headers.get("Origin")
        return (host in hosts and (not origin or origin.lower() == f"http://{host}")
                and self.headers.get("Sec-Fetch-Site") != "cross-site")

    def read_json(self, required):
        if self.headers.get("Transfer-Encoding"):
            raise ValueError("Chunked request bodies are not supported.")
        length = int(self.headers.get("Content-Length", "0"))
        limit = 7 * MAX_BODY if self.path == "/api/documents/import" else MAX_BODY
        if not 0 <= length <= limit:
            raise ValueError("Request body exceeds the upload limit.")
        if not length and not required:
            return {}
        if self.headers.get_content_type() != "application/json":
            raise ValueError("Send an application/json request.")
        data = json.loads(self.rfile.read(length))
        if not isinstance(data, dict):
            raise ValueError("Expected a JSON object.")
        # Reject unpaired surrogates before values can reach SQLite or UTF-8 responses.
        json.dumps(data, ensure_ascii=False, allow_nan=False).encode()
        return data

    def serve_static(self, path):
        if path == "/":
            path = "/index.html"
        pieces = path.lstrip("/").split("/")
        if any(part.startswith(".") or "\\" in part or ":" in part for part in pieces):
            return self.json(404, {"error": "File not found."})
        file = WEB_ROOT.joinpath(*pieces).resolve()
        if not file.is_relative_to(WEB_ROOT) or not file.is_file():
            return self.json(404, {"error": "File not found."})
        data = file.read_bytes()
        mime = {".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css"}.get(file.suffix)
        mime = mime or mimetypes.guess_type(file.name)[0] or "application/octet-stream"
        self.headers_for(200, mime, len(data))
        if self.command != "HEAD":
            self.wfile.write(data)

    def chat(self, body):
        app = self.server.app
        signal = Cancellation()
        finished = threading.Event()

        def watch_disconnect():
            # Abort upstream generation during model loading, before any token write detects a lost client.
            while not finished.wait(0.15):
                try:
                    readable = select.select([self.connection], [], [], 0)[0]
                    if not readable or self.connection.recv(1, socket.MSG_PEEK):
                        continue
                except OSError:
                    pass
                signal.stop("Response stopped when the client disconnected.")
                return

        stream = app.engine.generate(body.get("prompt"), app.store.settings(),
                                     conversation_id=body.get("conversation_id"), cancellation=signal)
        try:
            first = next(stream)  # validation and turn reservation before HTTP 200
            self.headers_for(200, "application/x-ndjson; charset=utf-8")
            threading.Thread(target=watch_disconnect, name="client-disconnect", daemon=True).start()
            for packet in chain((first,), stream):
                self.wfile.write((json.dumps(packet, ensure_ascii=False) + "\n").encode())
                self.wfile.flush()
        finally:
            finished.set()
            stream.close()

    def dispatch(self):
        try:
            if not self.local_request():
                return self.json(403, {"error": "Use the local Ember Den address."})
            path = unquote(urlsplit(self.path).path)
            if not path.startswith("/api/"):
                if self.command in ("GET", "HEAD"):
                    return self.serve_static(path)
                return self.json(405, {"error": "Method not allowed."})
            body = self.read_json(self.command in ("POST", "PATCH"))
            if path == "/api/chat" and self.command == "POST":
                return self.chat(body)
            result = self.server.app.route(self.command, path, body)
            self.json(*(result or (404, {"error": "Route not found."})))
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except ConflictError as exc:
            self.json(409, {"error": str(exc)})
        except KeyError as exc:
            self.json(404, {"error": str(exc.args[0])})
        except (ValueError, RecursionError) as exc:
            self.json(400, {"error": str(exc)})
        except ProviderError as exc:
            self.json(503, {"error": str(exc)})
        except Exception as exc:
            print(f"Request failed ({type(exc).__name__}).", file=sys.stderr)
            self.json(500, {"error": "The local service could not complete the request."})

    do_GET = do_HEAD = do_POST = do_PATCH = do_DELETE = dispatch


@contextmanager
def data_lock(directory):
    """Only one process may recover and write a given personal database at a time."""
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "instance.lock").open("a+b") as handle:
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError("Ember Den already has this data directory open.") from exc
        yield


def default_data_dir():
    root = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share"
    return Path(root) / "Ember Den"


def serve(port, data_dir, on_ready=None):
    with data_lock(Path(data_dir)), ThreadingHTTPServer(("127.0.0.1", port), Handler) as server:
        server.app = Application(data_dir)
        url = f"http://127.0.0.1:{server.server_port}"
        print(f"Ember Den is ready at {url}", flush=True)
        if on_ready:
            on_ready(url)
        server.serve_forever()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-dir", type=Path, default=default_data_dir())
    args = parser.parse_args()
    try:
        serve(args.port, args.data_dir)
    except KeyboardInterrupt:
        pass
    except (OSError, RuntimeError) as exc:
        parser.exit(1, str(exc) + "\n")


if __name__ == "__main__":
    main()
