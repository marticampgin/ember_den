import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from den.server import default_data_dir
from den.speech_models import PIPER, VOSK, SpeechModels
from den.voice import Voice


class NativeStub:
    def run(self, operation, values):
        if operation == "status":
            return {"voices":["Example"], "recognizers":["en-US"]}
        return {"operation":operation, "choice":values.get("voice")}


class SpeechModelsTests(unittest.TestCase):
    def test_native_works_without_optional_runtime(self):
        with TemporaryDirectory() as directory:
            adapter = SpeechModels(Path(directory), NativeStub())
            self.assertEqual(adapter.run("status", {})["voices"], ["Example"])
            self.assertEqual(adapter.run("synthesize", {"voice":"Example"})["choice"], "Example")

    @unittest.skipUnless(os.getenv("EMBER_TEST_SPEECH_MODELS") == "1", "Optional offline models")
    def test_installed_cpu_models_round_trip(self):
        adapter = SpeechModels(default_data_dir())
        inventory = adapter.status()
        self.assertIn(VOSK, [option["id"] for option in inventory["recognizer_options"]])
        self.assertIn(PIPER, inventory["voices"])
        voice = Voice(adapter)
        code, clip = voice.route("POST","/api/voice/synthesize",
                                        {"voice":PIPER, "text":"The red fox is sleeping in the forest."})
        self.assertEqual(code, 200, clip.get("error"))
        self.assertEqual(clip["voice"], PIPER)
        code, result = voice.route("POST","/api/voice/transcribe", {"recognizer":VOSK, "audio":clip["audio"]})
        self.assertEqual(code, 200, result.get("error"))
        self.assertEqual(result["recognizer"], VOSK)
        self.assertIn("red fox",result["text"].lower())
