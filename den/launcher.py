"""Windows double-click entry point. Reuse the existing local services when possible."""

import http.client
import os
import shutil
import subprocess
import sys
import time
import webbrowser
from contextlib import closing

from .server import default_data_dir, serve


PORT = 8765
URL = f"http://127.0.0.1:{PORT}/"


def service(port, path, method="GET"):
    try:
        with closing(http.client.HTTPConnection("127.0.0.1", port, timeout=1)) as connection:
            connection.request(method, path)
            response = connection.getresponse()
            return response.status, response.getheader("Server", "")
    except (OSError, http.client.HTTPException):
        return None


def main():
    running = service(PORT, "/", "HEAD")
    if running:
        if running[0] == 200 and running[1].startswith("EmberDen"):
            webbrowser.open(URL)
            return 0
        print(f"Port {PORT} is busy. To use another port, run python -m den.server --port 8766.")
        return 1

    if service(11434, "/api/version") is None and (ollama := shutil.which("ollama")):
        env = {**os.environ, "OLLAMA_HOST": "127.0.0.1:11434", "OLLAMA_NO_CLOUD": "1"}
        try:
            proc = subprocess.Popen([ollama, "serve"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, env=env,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            for _ in range(25):
                if service(11434, "/api/version") or proc.poll() is not None:
                    break
                time.sleep(0.2)
        except OSError as error:
            print(f"Ollama could not start: {error}")
    print("Keep this window open while using Ember Den. Press Ctrl+C here to stop it.")
    try:
        serve(PORT, default_data_dir(), on_ready=webbrowser.open)
    except KeyboardInterrupt:
        pass
    except (RuntimeError, OSError) as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
