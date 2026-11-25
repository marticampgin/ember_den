"""Exercise the real web app in an isolated database, optionally with local inference."""

import argparse
import json
import re
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def test_server():
    with tempfile.TemporaryDirectory(prefix="ember-browser-") as directory:
        process = subprocess.Popen(
            [sys.executable, "-u", "-m", "den.server", "--port", "0", "--data-dir", directory],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, cwd=ROOT,
        )
        try:
            startup = process.stdout.readline()
            match = re.search(r"http://127\.0\.0\.1:\d+", startup)
            if not match:
                raise RuntimeError(f"Server failed to start: {startup}")
            yield match.group()
        finally:
            process.terminate()
            process.wait(timeout=10)
            process.stdout.close()


def check_layout(page):
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "Horizontal page overflow"


def capture(page, directory, name):
    check_layout(page)
    if directory:
        directory.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=directory / f"{name}.png")


def run(page, url, options):
    faults = []
    page.on("pageerror", lambda error: faults.append(str(error)))
    page.on("console", lambda message: faults.append(message.text) if message.type in ("error", "warning") else None)
    page.goto(url)
    expect(page).to_have_title("The clearing · Ember Den")
    scene = page.locator("ember-scene")
    expect(scene).to_have_attribute("state", "idle")
    expect(scene).to_have_attribute("data-pose", "idle")
    assert scene.locator("canvas").evaluate("c => c.getContext('2d').getImageData(65,118,1,1).data[3] > 0")
    expect(page.locator("#runtime-label")).not_to_have_text("Checking runtime")
    capture(page, options.screenshots, "companion-desktop")
    page.set_viewport_size({"width": 1366, "height": 768})
    send = page.get_by_role("button", name="Send message").bounding_box()
    assert send and send["y"] + send["height"] <= 768, "Composer falls below the initial laptop viewport"
    check_layout(page)
    page.set_viewport_size({"width": 1536, "height": 1024})
    prompt = page.get_by_role("textbox", name="Message Ember")
    prompt.fill("Hello, Ember")
    expect(scene).to_have_attribute("state", "listening")
    capture(page, options.screenshots, "companion-awake")
    prompt.fill("/calc (19 + 7) * 3")
    prompt.press("Enter")
    expect(page.locator(".from-ember .message-body")).to_have_text("78")
    capture(page, options.screenshots, "companion-conversation")
    page.reload()
    expect(page.locator(".from-ember .message-body")).to_have_text("78")
    page.get_by_role("link", name="Memories", exact=True).click()
    page.locator("ember-library input[type=file]").set_input_files({
        "name": "field-notes.md", "mimeType": "text/markdown",
        "buffer": b"# River station\nThe kingfisher station callsign is Juniper-47.",
    })
    page.get_by_role("button", name="field-notes.md", exact=True).click()
    expect(page.get_by_role("dialog")).to_contain_text("Juniper-47")
    page.get_by_role("button", name="Close dialog").click()
    if options.live:
        live_conversation(page, url, options)
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(url)
    expect(page.get_by_role("button", name="Open navigation")).to_be_visible()
    expect(page.locator("#model-name")).not_to_have_text("Checking…")
    page.emulate_media(reduced_motion="reduce")
    expect(page.locator("ember-scene canvas")).to_have_css("animation-name", "none")
    capture(page, options.screenshots, "companion-mobile")
    page.get_by_role("button", name="Open navigation").click()
    page.get_by_role("link", name="Laboratory", exact=True).click()
    expect(page.get_by_role("heading", name="New comparison")).to_be_visible()
    check_layout(page)
    assert not faults, faults


def live_conversation(page, url, options):
    status = page.request.get(url + "/api/status").json()
    assert status["online"] and status["models"], "The live check requires an installed Ollama chat model."
    page.get_by_role("button", name="New conversation").click()
    prompt = page.get_by_role("textbox", name="Message Ember")
    prompt.fill("What is the kingfisher station callsign? Include the callsign and its source in one sentence.")
    page.evaluate("""() => {
        window.observedPoses = new Set();
        const scene = document.querySelector('ember-scene');
        new MutationObserver(entries => {
            entries.forEach(entry => window.observedPoses.add(entry.oldValue));
            window.observedPoses.add(scene.getAttribute('state'));
        }).observe(scene, {attributes:true, attributeOldValue:true, attributeFilter:['state']});
    }""")
    prompt.press("Enter")
    reply = page.locator(".from-ember .message-body")
    expect(reply).to_contain_text("Juniper-47", ignore_case=True, timeout=180000)
    expect(page.locator("#transcript")).to_have_attribute("aria-busy", "false", timeout=180000)
    observed = page.evaluate("Array.from(window.observedPoses)")
    assert {"thinking", "responding", "idle"}.issubset(observed), observed
    run_id = page.request.get(url + "/api/runs").json()["runs"][0]["id"]
    run = page.request.get(url + "/api/runs/" + run_id).json()
    assert run["status"] == "completed" and run["metrics"]["ttft_ms"] > 0, run["error"]
    assert any(ref.get("name") == "field-notes.md" for ref in run["context"])
    page.get_by_role("link", name="Laboratory", exact=True).click()
    page.get_by_role("button", name="Run traces").click()
    page.locator(f'[data-item="{run_id}"]').click()
    page.get_by_text(re.compile(r"Retrieved references \(")).click()
    expect(page.locator(".lab-references")).to_contain_text("field-notes.md")
    page.locator('[data-action="use-trace"]').click()
    expect(page.get_by_label("Test prompt")).to_contain_text("kingfisher")
    seed = page.locator('input[name="seed0"]')
    expect(seed).to_have_value("")
    expect(seed).to_have_attribute("placeholder", "Runtime default")
    assert not seed.evaluate("el => el.required")
    second = page.locator('select[name="model1"]')
    alternate = "LiquidAI/lfm2.5-2.6b:latest"
    if alternate in {model["name"] for model in status["models"]} and alternate != run["model"]:
        second.select_option(alternate)
    page.locator(".lab-check summary").click()
    page.locator('input[name="expected"]').fill("Juniper-47")
    capture(page, options.screenshots, "laboratory-workbench")
    page.get_by_role("button", name="Run comparison", exact=True).click()
    expect(page.locator(".lab-result")).to_have_count(2, timeout=30000)
    expect(page.locator(".lab-result [data-state='completed']")).to_have_count(2, timeout=240000)
    results_id = page.request.get(url + "/api/lab/experiments").json()["experiments"][0]["id"]
    result = page.request.get(url + "/api/lab/experiments/" + results_id).json()
    assert result["snapshot"]["configs"][0]["seed"] is None
    assert all(arm["run"]["context"] == run["context"] for arm in result["results"])
    assert all(arm["check"] is True for arm in result["results"]), "An answer omitted the callsign"
    assert page.request.get(url + "/api/settings").json() == status["settings"]
    expect(page.locator("#toast")).to_be_hidden(timeout=8000)
    capture(page, options.screenshots, "laboratory-results")
    print(json.dumps({"chat": run["metrics"], "comparison": [
        {"model": arm["run"]["model"], "metrics": arm["run"]["metrics"], "passed": arm["check"]}
        for arm in result["results"]
    ]}, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Also run a real grounded chat and two-model comparison.")
    parser.add_argument("--browser", help="Installed Chromium-family browser executable.")
    parser.add_argument("--screenshots", type=Path, help="Optional output directory for review images.")
    args = parser.parse_args()
    with test_server() as url, sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=args.browser)
        try:
            page = browser.new_page(viewport={"width": 1536, "height": 1024}, device_scale_factor=1)
            run(page, url, args)
        finally:
            browser.close()
    print("Browser smoke checks passed.")


if __name__ == "__main__":
    main()
