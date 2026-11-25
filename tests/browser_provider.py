"""Verify the second transport with a real llama-server process and a user-supplied GGUF."""

import argparse
from contextlib import ExitStack, closing
import http.client
import json
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import time

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
ALIAS = "ember-provider-check"


def start(stack, command, **options):
    process = subprocess.Popen(
        command,
        cwd=ROOT,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        **options,
    )

    def stop():
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=8)

    stack.callback(stop)
    return process


def run(binary, weights, browser_path, screenshot=None):
    with ExitStack() as stack:
        data = Path(
            stack.enter_context(tempfile.TemporaryDirectory(prefix="ember-llama-test-"))
        )
        log = stack.enter_context((data / "llama-server.log").open("w+b"))
        with closing(socket.socket()) as free:
            free.bind(("127.0.0.1", 0))
            port = free.getsockname()[1]
        runtime = start(
            stack,
            [
                str(binary),
                "--model",
                str(weights),
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--alias",
                ALIAS,
                "--ctx-size",
                "2048",
                "--threads",
                "4",
                "--gpu-layers",
                "0",
                "--parallel",
                "1",
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline and runtime.poll() is None:
            try:
                with closing(
                    http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                ) as connection:
                    connection.request("GET", "/health")
                    if connection.getresponse().status == 200:
                        break
            except OSError:
                pass
            time.sleep(0.25)
        else:
            log.seek(0)
            raise RuntimeError(
                "llama-server did not become ready: "
                + log.read()[-2500:].decode(errors="replace")
            )
        backend = start(
            stack,
            [
                sys.executable,
                "-u",
                "-m",
                "den.server",
                "--port",
                "0",
                "--data-dir",
                str(data / "db"),
                "--provider",
                "auto",
                "--provider-port",
                str(port),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        startup = backend.stdout.readline()
        match = re.search(r"http://127\.0\.0\.1:\d+", startup)
        if not match:
            raise RuntimeError("Ember server did not start: " + startup)
        url = match.group()
        pw = stack.enter_context(sync_playwright())
        browser = pw.chromium.launch(executable_path=browser_path)
        stack.callback(browser.close)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.goto(url + "/#settings")
        selected = "local-api/" + ALIAS
        page.get_by_label("Active model").select_option(selected)
        page.locator("form button[type=submit]").first.click()
        expect(page.get_by_text("Settings saved.")).to_be_visible()
        page.get_by_role("link", name="Companion", exact=True).click()
        prompt = page.get_by_role("textbox", name="Message Ember")
        prompt.fill("Reply with the single word Blue.")
        prompt.press("Enter")
        reply = page.locator(".from-ember .message-body").last
        expect(reply).to_contain_text("blue", ignore_case=True, timeout=90000)
        expect(page.locator("#transcript")).to_have_attribute(
            "aria-busy", "false", timeout=90000
        )
        trace_id = page.request.get(url + "/api/runs").json()["runs"][0]["id"]
        trace = page.request.get(url + "/api/runs/" + trace_id).json()
        assert trace["status"] == "completed", trace["error"]
        assert (
            trace["model"] == selected and "compatible" in trace["provider"].lower()
        ), trace
        assert (
            trace["metrics"]["output_tokens"] > 0 and trace["metrics"]["ttft_ms"] > 0
        ), trace["metrics"]
        assert trace["metrics"].get("resident_vram_bytes") is None
        page.goto(url + "/#laboratory")
        page.get_by_label("Test prompt").fill("Reply with only the word Blue.")
        page.locator("[name=model0]").select_option(selected)
        other = page.request.get(url + "/api/status").json()["models"]
        native = next((m for m in other if m["name"] == "qwen3.5:2b"), None)
        page.locator("[name=model1]").select_option(
            native["name"] if native else selected
        )
        page.get_by_role("button", name="Run comparison", exact=True).click()
        expect(page.locator(".lab-result [data-state=completed]")).to_have_count(
            2, timeout=180000
        )
        latest = page.request.get(url + "/api/lab/experiments").json()["experiments"][0]
        experiment = page.request.get(
            url + "/api/lab/experiments/" + latest["id"]
        ).json()
        assert experiment["results"][0]["run"]["provider"] == trace["provider"]
        if native:
            assert experiment["results"][1]["run"]["provider"] == "Ollama"
        if screenshot:
            screenshot.parent.mkdir(parents=True, exist_ok=True)
            page.evaluate("window.scrollTo(0,0)")
            page.screenshot(path=screenshot, full_page=True)
        assert not errors, errors
        print(
            json.dumps(
                {
                    "chat": trace["response"],
                    "runtime": trace["provider"],
                    "metrics": trace["metrics"],
                    "experiment": [r["run"]["provider"] for r in experiment["results"]],
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--llama-server", required=True, type=Path)
    parser.add_argument(
        "--model", required=True, type=Path, help="Local chat-capable GGUF."
    )
    parser.add_argument("--browser")
    parser.add_argument("--screenshot", type=Path)
    args = parser.parse_args()
    run(
        args.llama_server.resolve(), args.model.resolve(), args.browser, args.screenshot
    )
