"""Local English voice adapter. PCM and transcripts live only in request memory."""

import base64
import binascii
import io
import json
import os
from pathlib import Path
import subprocess
import threading
import time
import wave


RATE = 16_000
MAX_SECONDS = 20
MAX_SAMPLES = RATE * MAX_SECONDS
MAX_TEXT = 500


class SpeechUnavailable(Exception):
    pass


def pcm_from_request(encoded):
    if not isinstance(encoded, str) or len(encoded) > 860_000:
        raise ValueError("Send up to 20 seconds of English audio.")
    try:
        data = base64.b64decode(encoded, validate=True)
        with wave.open(io.BytesIO(data), "rb") as clip:
            if (clip.getnchannels(), clip.getsampwidth(), clip.getframerate()) != (1, 2, RATE):
                raise ValueError("Record mono, 16 kHz, 16-bit PCM audio.")
            frames = clip.getnframes()
            if not RATE // 5 <= frames <= MAX_SAMPLES:
                raise ValueError("Record between 0.2 and 20 seconds of audio.")
            raw = clip.readframes(frames)
            if len(raw) != frames * 2:
                raise ValueError("The recording is incomplete.")
            return raw
    except (binascii.Error, wave.Error, EOFError) as exc:
        raise ValueError("The recording is not a readable PCM WAV file.") from exc


def pcm_wave(raw):
    output = io.BytesIO()
    with wave.open(output, "wb") as clip:
        clip.setparams((1, 2, RATE, 0, "NONE", "not compressed"))
        clip.writeframes(raw)
    return output.getvalue()


class WindowsSpeech:
    def run(self, operation, values):
        if os.name != "nt":
            raise SpeechUnavailable("This speech adapter requires Windows with English speech installed.")
        executable = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        program = Path(__file__).with_name("windows_speech.ps1").read_text(encoding="utf-8")
        # Only the packaged program is executable. All user data crosses standard input as JSON.
        try:
            process = subprocess.run(
                [str(executable), "-NoProfile", "-NonInteractive", "-Command", program],
                input=json.dumps({"operation": operation, **values}).encode("ascii"),
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                timeout=35, creationflags=subprocess.CREATE_NO_WINDOW, check=False,
            )
            result = json.loads(process.stdout.decode("utf-8-sig"))
            if process.returncode or not isinstance(result, dict):
                raise SpeechUnavailable("The Windows speech service could not complete this request.")
            if "error" in result:
                raise SpeechUnavailable(result["error"])
            return result
        except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
            raise SpeechUnavailable("Windows speech is unavailable. Check the installed English speech pack.") from exc


class Voice:
    def __init__(self, adapter=None):
        self.adapter = adapter or WindowsSpeech()
        self._slots = threading.BoundedSemaphore(2)
        self._status_lock = threading.Lock()
        self._status = None
        self._expires = 0

    def status(self):
        with self._status_lock:
            if self._status is not None and time.monotonic() < self._expires:
                return self._status
            defaults = {"provider": "Windows Speech", "recognizers": [], "voices": [],
                        "sample_rate": RATE, "max_seconds": MAX_SECONDS}
            try:
                defaults.update(self.adapter.run("status", {}))
            except SpeechUnavailable as exc:
                defaults["error"] = str(exc)
            self._status = defaults
            self._expires = time.monotonic() + 30
            return defaults

    def route(self, method, path, body):
        if method == "GET" and path == "/api/voice/status":
            return 200, self.status()
        if method != "POST" or path not in ("/api/voice/transcribe", "/api/voice/synthesize"):
            return None
        if not self._slots.acquire(blocking=False):
            return 429, {"error": "Both local speech workers are busy. Try again in a moment."}
        try:
            start = time.perf_counter()
            if path.endswith("/transcribe"):
                pcm = pcm_from_request(body.get("audio"))
                recognizer = body.get("recognizer", "")
                if not isinstance(recognizer, str) or len(recognizer) > 300:
                    raise ValueError("Choose an installed English recognizer.")
                result = self.adapter.run("transcribe", {"pcm": base64.b64encode(pcm).decode("ascii"),
                                                          "recognizer": recognizer})
                if not result.get("text"):
                    return 422, {"error": "No clear English speech was recognized. Try again or type your message."}
                result["duration_ms"] = round(len(pcm) * 500 / RATE)
            else:
                text, voice = body.get("text"), body.get("voice", "")
                if not isinstance(text, str) or not 1 <= len(text.strip()) <= MAX_TEXT:
                    raise ValueError("Use 1 to 500 characters per speech segment.")
                if not isinstance(voice, str) or len(voice) > 200:
                    raise ValueError("Choose an installed English voice.")
                result = self.adapter.run("synthesize", {"text": text.strip(), "voice": voice})
                pcm = base64.b64decode(result.pop("pcm"), validate=True)
                result["audio"] = base64.b64encode(pcm_wave(pcm)).decode("ascii")
                result["duration_ms"] = round(len(pcm) * 500 / RATE)
            result["elapsed_ms"] = round((time.perf_counter() - start) * 1000)
            return 200, result
        except SpeechUnavailable as exc:
            return 503, {"error": str(exc)}
        finally:
            self._slots.release()

