import base64
import os
import unittest

from den.voice import MAX_SAMPLES, RATE, Voice, SpeechUnavailable, pcm_from_request, pcm_wave


class Adapter:
    def __init__(self):
        self.calls = []

    def run(self, operation, body):
        self.calls.append((operation, body))
        if operation == "status":
            return {"voices": ["English"], "recognizers": ["en-US"]}
        if operation == "synthesize":
            return {"pcm": base64.b64encode(bytes(RATE * 2)).decode(), "voice": "English"}
        return {"text": "a test", "language": "en-US"}


class VoiceTests(unittest.TestCase):
    def setUp(self):
        self.adapter = Adapter()
        self.voice = Voice(self.adapter)

    def test_snapshot_and_cache(self):
        self.assertEqual(self.voice.status()["recognizers"], ["en-US"])
        self.assertEqual(self.voice.status()["max_seconds"], 20)
        self.assertEqual(len(self.adapter.calls), 1)

    def test_synthesized_pcm_round_trip(self):
        code, clip = self.voice.route("POST", "/api/voice/synthesize", {"text": "a test", "voice": "English"})
        self.assertEqual(code, 200)
        self.assertEqual(clip["duration_ms"], 1000)
        self.assertEqual(len(pcm_from_request(clip["audio"])), RATE * 2)
        code, result = self.voice.route("POST", "/api/voice/transcribe", {"audio": clip["audio"]})
        self.assertEqual((code, result["text"], result["duration_ms"]), (200, "a test", 1000))
        self.voice.route("POST", "/api/voice/transcribe", {"audio": clip["audio"], "recognizer": "custom-installed-id"})
        self.assertEqual(self.adapter.calls[-1][1]["recognizer"], "custom-installed-id")
        with self.assertRaises(ValueError):
            self.voice.route("POST", "/api/voice/transcribe", {"audio": clip["audio"], "recognizer": []})
        self.assertGreaterEqual(result["elapsed_ms"], 0)

    def test_invalid_inputs_do_not_reach_native_engine(self):
        for audio in ("file:///private.wav", "", "YQ==", None,
                      base64.b64encode(pcm_wave(bytes(20))).decode(),
                      base64.b64encode(pcm_wave(bytes((MAX_SAMPLES + 1) * 2))).decode()):
            with self.subTest(size=len(audio) if audio else 0), self.assertRaises(ValueError):
                self.voice.route("POST", "/api/voice/transcribe", {"audio": audio})
        with self.assertRaises(ValueError):
            self.voice.route("POST", "/api/voice/synthesize", {"text": "a" * 501})
        self.assertFalse(self.adapter.calls)

    def test_unavailable_and_empty_results(self):
        def unavailable(*args):
            raise SpeechUnavailable("English speech missing")

        self.adapter.run = unavailable
        self.assertEqual(self.voice.status()["voices"], [])
        self.assertIn("missing", self.voice.status()["error"])
        self.assertEqual(self.voice.route("POST", "/api/voice/synthesize", {"text": "a"})[0], 503)
        self.adapter.run = lambda *args: {"text": ""}
        clip = base64.b64encode(pcm_wave(bytes(RATE * 2))).decode()
        self.assertEqual(self.voice.route("POST", "/api/voice/transcribe", {"audio": clip})[0], 422)

    @unittest.skipUnless(os.environ.get("EMBER_TEST_WINDOWS_SPEECH") == "1", "set EMBER_TEST_WINDOWS_SPEECH=1 for native integration")
    def test_windows_english_round_trip(self):
        voice = Voice()
        status = voice.status()
        self.assertTrue(status["recognizers"])
        for option in status["recognizer_options"]:
            self.assertTrue(option["id"])
            self.assertIn(option["culture"],status["recognizers"])
        code, clip = voice.route("POST", "/api/voice/synthesize", {"text": "Tell me a story about the moon."})
        self.assertEqual(code, 200, clip.get("error"))
        code, result = voice.route("POST", "/api/voice/transcribe", {"audio": clip["audio"]})
        self.assertEqual(code, 200, result)
        self.assertIn("moon", result["text"].casefold())
        first = status["recognizer_options"][0]
        for selection in (first["id"], first["culture"], "removed-recognizer"):
            with self.subTest(selection=selection):
                code, recognized = voice.route("POST", "/api/voice/transcribe", {"audio":clip["audio"],"recognizer":selection})
                self.assertEqual(code,200,recognized)
                self.assertEqual(recognized["recognizer"],first["id"])
        for name in (status["voices"][-1], "Removed historical TTS"):
            code, generated = voice.route("POST", "/api/voice/synthesize", {"text":"Hello.","voice":name})
            self.assertEqual(code,200,generated.get("error"))
            self.assertEqual(generated["voice"], name if name in status["voices"] else status["default_voice"])


if __name__ == "__main__":
    unittest.main()

