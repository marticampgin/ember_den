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
    payload = json.dumps({"text": "Tell me a story about the moon."}).encode()
    request = Request(origin + "/api/voice/synthesize", data=payload, headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=45) as response:
        clip = json.load(response)
    with wave.open(io.BytesIO(base64.b64decode(clip["audio"])), "rb") as source:
        with wave.open(str(wav_path), "wb") as wav:
            wav.setparams(source.getparams())
            silence = bytes(source.getframerate() * 2)
            wav.writeframes(silence[:8000] + source.readframes(source.getnframes()) + silence * 3)


def hands_free(page, origin, screenshots, speech_models):
    recorded = []
    def speech_result(response):
        if response.url in (origin + "/api/voice/transcribe", origin + "/api/voice/synthesize") and response.ok:
            data = response.json()
            recorded.append({"engine":data.get("recognizer") or data.get("voice"),"elapsed_ms":data["elapsed_ms"]})
    page.on("response",speech_result)
    prompt = page.get_by_role("textbox", name="Message Ember")
    page.request.patch(origin + "/api/settings", data={"system_prompt":"Answer every request using the single word Moon."})
    # Observe physical capture lifetimes independently of the visible recording indicator.
    page.evaluate("""() => {
        window.speechInputs = [];
        const original = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
        navigator.mediaDevices.getUserMedia = async options => {
            const stream = await original(options);
            window.speechInputs.push(stream);
            return stream;
        };
        window.speechPhases = [];
        const label = document.querySelector('[data-voice-status]');
        new MutationObserver(() => window.speechPhases.push({
            status: label.textContent,
            live: window.speechInputs.some(s => s.getTracks().some(t => t.readyState === 'live')),
        })).observe(label, {childList:true, subtree:true});
    }""")
    prompt.fill("A draft for later")
    page.get_by_role("button", name="Start hands-free").click()
    expect(page.locator("[data-voice-status]")).to_contain_text("Pause to send automatically")
    end = page.get_by_role("button", name="End hands-free")
    expect(end).to_be_visible()
    if screenshots:
        page.screenshot(path=screenshots / "hands-free-mobile.png")
    expect(page.locator(".from-user .message-body")).to_have_count(1,timeout=35000)
    expect(page.locator(".from-ember .message-body")).not_to_be_empty(timeout=120000)
    try:
        expect(page.locator("[data-voice-status]")).to_contain_text("Pause to send automatically",timeout=90000)
    except AssertionError:
        print("Voice state history:", json.dumps(page.evaluate("speechPhases")))
        print("Reply:",json.dumps(page.locator(".from-ember .message-body").inner_text()[:150]))
        raise
    # The first reply has finished playing and the microphone has reopened for turn two.
    expect(page.locator("[data-voice-status]")).to_contain_text("Pause to send automatically")
    phases = page.evaluate("speechPhases")
    assert page.evaluate("speechInputs.length >= 2")
    assert any("Speaking." in e["status"] for e in phases), phases
    assert not any(e["live"] and ("Speaking." in e["status"] or "Waiting for Ember" in e["status"]) for e in phases), phases
    end.click()
    expect(prompt).to_have_value("A draft for later")
    expect(page.get_by_role("button",name="Start hands-free")).to_be_visible()
    assert page.evaluate("speechInputs.every(s => s.getTracks().every(t => t.readyState === 'ended'))")
    expect(page.locator(".from-user .message-body")).to_have_count(1)
    page.get_by_role("button",name="Start hands-free").click()
    expect(page.locator("[data-voice-status]")).to_contain_text("Pause to send automatically")
    page.evaluate("""() => {
        Object.defineProperty(document, 'hidden', {configurable:true, value:true});
        document.dispatchEvent(new Event('visibilitychange'));
        delete document.hidden;
    }""")
    expect(page.locator("[data-voice-status]")).to_contain_text("tab was hidden")
    assert page.evaluate("speechInputs.every(s => s.getTracks().every(t => t.readyState === 'ended'))")
    page.remove_listener("response",speech_result)
    if speech_models:
        assert {"vosk-small-en-us-0.15", "Piper · Lessac"}.issubset({row["engine"] for row in recorded}), recorded
    spoken = next(entry["status"] for entry in phases if "Speaking." in entry["status"])
    print("Hands-free engines:",json.dumps(recorded),"timing:",spoken)
    # Leaving the companion while actively recording must close its stream.
    page.get_by_role("button", name="Start hands-free").click()
    expect(page.locator("[data-voice-status]")).to_contain_text("Pause to send automatically")
    page.get_by_role("button",name="Open navigation").click()
    page.get_by_role("link",name="Settings",exact=True).click()
    expect(page.locator("#voice-recognizer")).to_be_visible()
    assert page.evaluate("speechInputs.every(s => s.getTracks().every(t => t.readyState === 'ended'))")
    print("Hands-free: submitted speech, closed input for playback, rearmed, restored draft, stopped on hidden tab and navigation.")
    page.goto(origin)


def run(screenshots, speech_models=False):
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
        page.evaluate("""() => {
            localStorage.setItem('ember.voice.device','retired-microphone');
            localStorage.setItem('ember.voice.voice','retired-voice');
            localStorage.setItem('ember.voice.recognizer','en-US');
            const open = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
            navigator.mediaDevices.getUserMedia = async constraints => {
                if (constraints.audio.deviceId?.exact === 'retired-microphone')
                    throw new DOMException('Removed device','OverconstrainedError');
                return open(constraints);
            };
        }""")
        page.get_by_role("button", name="Dictate a message").click()
        expect(page.get_by_role("button", name="Finish dictation")).to_be_visible(timeout=10000)
        expect(page.locator("ember-scene")).to_have_attribute("state", "listening")
        prompt = page.get_by_role("textbox", name="Message Ember")
        expect(page.locator("[data-voice-status]")).to_contain_text("Review and send", timeout=35000)
        heard = prompt.input_value()
        # Dictation is probabilistic. Require a close transcription and exercise review when needed.
        target = "Tell me a story about the moon"
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
        assert not page.evaluate("localStorage.getItem('ember.voice.device')")
        assert not page.evaluate("localStorage.getItem('ember.voice.voice')")
        speech = page.request.get(origin + "/api/voice/status").json()
        selected = page.evaluate("localStorage.getItem('ember.voice.recognizer')")
        assert selected == next(item["id"] for item in speech["recognizer_options"] if item["culture"]=="en-US")
        page.get_by_role("button", name="Read aloud").click()
        expect(page.get_by_role("button", name="Stop voice")).to_be_visible()
        page.get_by_role("button", name="Stop voice").click()
        expect(page.locator("[data-voice-status]")).to_have_text("Voice stopped.")
        page.get_by_role("link", name="Settings", exact=True).click()
        expect(page.locator("#voice-voice")).to_be_visible()
        expect(page.locator("#voice-recognizer")).to_have_value(selected)
        page.evaluate("localStorage.setItem('ember.voice.recognizer','removed-recognizer')")
        page.reload()
        expect(page.locator("#voice-recognizer")).to_have_value("")
        assert not page.evaluate("localStorage.getItem('ember.voice.recognizer')")
        page.locator("#voice-autoplay").uncheck()
        page.reload()
        expect(page.locator("#voice-autoplay")).not_to_be_checked()
        assert page.locator("#voice-voice option").count() > 1
        if speech_models:
            page.get_by_label("Speech recognizer").select_option("vosk-small-en-us-0.15")
            page.get_by_label("Spoken voice").select_option("Piper · Lessac")
            page.reload()
            expect(page.get_by_label("Speech recognizer")).to_have_value("vosk-small-en-us-0.15")
            expect(page.get_by_label("Spoken voice")).to_have_value("Piper · Lessac")
            if screenshots:
                page.locator(".voice-settings").screenshot(path=screenshots/"speech-models-settings.png")
        page.set_viewport_size({"width": 390, "height": 844})
        page.goto(origin)
        expect(page.get_by_role("button", name="Dictate a message")).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        if screenshots:
            page.wait_for_load_state("networkidle")
            page.screenshot(path=screenshots / "voice-mobile.png")
        hands_free(page, origin, screenshots, speech_models)
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
    parser.add_argument("--speech-models", action="store_true", help="Select the installed Vosk and Piper models in Settings for the hands-free turn.")
    options = parser.parse_args()
    run(options.screenshots, options.speech_models)
