"""Combine installed Windows speech with optional, isolated offline CPU models."""

import json
from pathlib import Path
import subprocess

from .voice import SpeechUnavailable, WindowsSpeech


VOSK = "vosk-small-en-us-0.15"
PIPER = "Piper · Lessac"
VOSK_MODEL = Path("vosk-model-small-en-us-0.15")
PIPER_MODEL = Path("en/en_US/lessac/medium/en_US-lessac-medium.onnx")


class SpeechModels:
    def __init__(self, root, native=None):
        self.root = Path(root)
        self.native = native or WindowsSpeech()
        self.models = self.root / "speech-models"
        self.python = self.root / "speech-runtime" / "Scripts" / "python.exe"

    def _installed(self):
        if not self.python.is_file():
            return False, False
        libraries = self.root / "speech-runtime" / "Lib" / "site-packages"
        asr = (self.models / VOSK_MODEL / "am/final.mdl").is_file() and (libraries / "vosk").is_dir()
        voice = self.models / PIPER_MODEL
        tts = voice.is_file() and voice.with_suffix(".onnx.json").is_file() and (libraries / "piper").is_dir()
        return asr, tts

    def status(self):
        asr, tts = self._installed()
        try:
            info = self.native.run("status", {})
        except SpeechUnavailable:
            if not (asr or tts):
                raise
            info = {"voices": [], "recognizers": []}
        if not (asr or tts):
            return info
        info["provider"] = "Local speech engines"
        if asr:
            info.setdefault("recognizer_options", [{"id":v,"name":v,"culture":v} for v in info["recognizers"]])
            info["recognizers"].append(VOSK)
            info["recognizer_options"].append({"id":VOSK,"name":"Vosk · Small US English","culture":"en-US"})
        if tts:
            info["voices"].append(PIPER)
        return info

    def run(self, operation, values):
        if operation == "status":
            return self.status()
        asr, tts = self._installed()
        use_vosk = operation == "transcribe" and values.get("recognizer") == VOSK and asr
        use_piper = operation == "synthesize" and values.get("voice") == PIPER and tts
        if not (use_vosk or use_piper):
            try:
                return self.native.run(operation, values)
            except SpeechUnavailable:
                # A machine can have the optional models without a Windows English speech pack.
                use_vosk = operation == "transcribe" and asr and not values.get("recognizer")
                use_piper = operation == "synthesize" and tts and not values.get("voice")
                if not (use_vosk or use_piper):
                    raise
        model = self.models / (VOSK_MODEL if use_vosk else PIPER_MODEL)
        worker = Path(__file__).with_name("speech_worker.py")
        try:
            result = subprocess.run(
                [str(self.python), "-I", str(worker), operation, str(model)],
                input=json.dumps(values).encode("ascii"), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                check=True, timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            data = json.loads(result.stdout)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            raise SpeechUnavailable("The selected local speech model could not process this recording or text.") from exc
        return {**data, **({"recognizer":VOSK,"language":"en-US"} if use_vosk else {"voice":PIPER})}
