"""Test actual Windows speech and Ollama using a synthetic microphone with no human audio."""

import argparse
import base64
import io
import json
from pathlib import Path
import tempfile
from difflib import SequenceMatcher
from urllib.request import Request, urlopen
import wave

from playwright.sync_api import expect, sync_playwright

from browser_smoke import test_server


def fresh_mic_file(origin, wav_path):
    payload = json.dumps({"text": "Say the word moon."}).encode()
    request = Request(origin + "/api/voice/synthesize", data=payload, headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=45) as response:
        clip = json.load(response)
    with wave.open(io.BytesIO(base64.b64decode(clip["audio"])), "rb") as source:
        with wave.open(str(wav_path), "wb") as wav:
            wav.setparams(source.getparams())
            silence = bytes(source.getframerate() * 2)
            wav.writeframes(silence[:8000] + source.readframes(source.getnframes()) + silence * 3)


def run(screenshots):
    with test_server() as origin, tempfile.TemporaryDirectory(prefix="ember-voice-") as temp, sync_playwright() as pw:
        fixture = Path(temp) / "input.wav"
        fresh_mic_file(origin, fixture)
        browser = pw.chromium.launch(args=[
            "--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream",
            "--use-file-for-fake-audio-capture=" + str(fixture),
        ])
        context = browser.new_context(permissions=["microphone"], viewport={"width": 1280, "height": 800})
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(origin)
        page.get_by_role("button", name="Dictate a message").click()
        expect(page.get_by_role("button", name="Finish dictation")).to_be_visible(timeout=10000)
        expect(page.locator("ember-scene")).to_have_attribute("state", "listening")
        prompt = page.get_by_role("textbox", name="Message Ember")
        expect(page.locator("[data-voice-status]")).to_contain_text("Review and send", timeout=35000)
        heard = prompt.input_value()
        # Dictation is probabilistic. Require a close transcription and exercise review when needed.
        target = "Say the word moon"
        assert SequenceMatcher(None, target.casefold(), heard.casefold()).ratio() >= 0.75, heard
        if "moon" not in heard.casefold():
            print("Reviewed transcription before sending:", json.dumps(heard))
            prompt.fill(target)
        if screenshots:
            screenshots.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=screenshots / "voice-dictated.png")
        page.get_by_role("button", name="Send message").click()
        expect(page.locator(".from-ember .message-body")).not_to_be_empty(timeout=120000)
        expect(page.locator("[data-voice-status]")).to_contain_text("Send to first audio", timeout=120000)
        print("Transcript:", heard)
        print("Reply:", json.dumps(page.locator(".from-ember .message-body").inner_text()[:200]))
        print("Timing:", page.locator("[data-voice-status]").inner_text())
        page.get_by_role("button", name="Read aloud").click()
        expect(page.get_by_role("button", name="Stop voice")).to_be_visible()
        page.get_by_role("button", name="Stop voice").click()
        expect(page.locator("[data-voice-status]")).to_have_text("Voice stopped.")
        page.get_by_role("link", name="Settings", exact=True).click()
        expect(page.locator("#voice-voice")).to_be_visible()
        page.locator("#voice-autoplay").uncheck()
        page.reload()
        expect(page.locator("#voice-autoplay")).not_to_be_checked()
        assert page.locator("#voice-voice option").count() > 1
        page.set_viewport_size({"width": 390, "height": 844})
        page.goto(origin)
        expect(page.get_by_role("button", name="Dictate a message")).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        if screenshots:
            page.wait_for_load_state("networkidle")
            page.screenshot(path=screenshots / "voice-mobile.png")
        page.get_by_role("button", name="Dictate a message").click()
        expect(page.get_by_role("button", name="Finish dictation")).to_be_visible()
        page.get_by_role("button", name="Cancel recording").click()
        expect(page.locator("#prompt")).to_have_value("")
        page.evaluate("""() => {
            navigator.mediaDevices.getUserMedia = async () => { throw new DOMException('Denied', 'NotAllowedError'); };
        }""")
        page.get_by_role("button", name="Dictate a message").click()
        expect(page.locator("[data-voice-status]")).to_contain_text("Microphone access was denied")
        expect(page.get_by_role("button", name="Dictate a message")).to_have_attribute("aria-pressed", "false")
        assert not errors, errors
        browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--screenshots", type=Path)
    run(parser.parse_args().screenshots)

