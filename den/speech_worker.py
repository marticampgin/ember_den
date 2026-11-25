"""One isolated CPU speech request. Reads JSON on stdin and retains no recording."""

import base64
import json
import sys


def transcribe(model_path, values):
    from vosk import KaldiRecognizer, Model, SetLogLevel

    SetLogLevel(-1)
    recognizer = KaldiRecognizer(Model(model_path), 16000)
    pcm = base64.b64decode(values["pcm"], validate=True)
    phrases = []
    for pos in range(0, len(pcm), 8000):
        if recognizer.AcceptWaveform(pcm[pos:pos+8000]):
            phrases.append(json.loads(recognizer.Result())["text"])
    phrases.append(json.loads(recognizer.FinalResult())["text"])
    return {"text":" ".join(phrase for phrase in phrases if phrase)}


def synthesize(model_path, values):
    import numpy as np
    from piper import PiperVoice

    voice = PiperVoice.load(model_path, use_cuda=False)
    audio = b"".join(chunk.audio_int16_bytes for chunk in voice.synthesize(values["text"]))
    # The browser adapter uses the same mono 16 kHz PCM contract for every voice.
    samples = np.frombuffer(audio, dtype="<i2")
    if samples.size == 0:
        raise ValueError("The voice produced no samples.")
    step = voice.config.sample_rate / 16000
    converted = np.interp(np.arange(0, len(samples), step), np.arange(len(samples)), samples).astype("<i2")
    return {"pcm":base64.b64encode(converted.tobytes()).decode("ascii")}


if __name__ == "__main__":
    methods = {"transcribe":transcribe, "synthesize":synthesize}
    json.dump(methods[sys.argv[1]](sys.argv[2], json.load(sys.stdin)), sys.stdout)
