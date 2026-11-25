# Offline speech models on Windows

Ember can mix and match a Windows English recognizer, a Windows English voice, Vosk Small US English, and the Piper Lessac voice. Under **Settings → Voice**, choose a speech recognizer and a voice separately. Dictation lets you review the transcript. **Start hands-free** sends each spoken turn after a pause, waits for Ember's spoken answer to finish, and reopens the microphone. **End hands-free** restores any draft you were writing.

The development machine already has both optional models. A fresh installation can use Windows speech immediately if the English speech pack is installed. The following PowerShell commands recreate the optional environment. They need Python 3.11+, the Hugging Face CLI and an internet connection during setup. The assistant uses the installed files offline.

```powershell
$speechRoot = Join-Path $env:LOCALAPPDATA "Ember Den"
$speechWeights = Join-Path $speechRoot "speech-models"
$speechEnv = Join-Path $speechRoot "speech-runtime"
py -3 -m venv $speechEnv
& "$speechEnv/Scripts/python.exe" -m pip install vosk==0.3.45 piper-tts==1.8.0

$voskArchive = Join-Path $env:TEMP "ember-vosk-small-en-us-0.15.zip"
curl.exe --fail --location --output $voskArchive https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip
if ((Get-FileHash $voskArchive).Hash -ne "30F26242C4EB449F948E42CB302DD7A686CB29A3423A8367F99FF41780942498") {
    throw "The downloaded Vosk archive does not match the tested version."
}
if (-not (Test-Path "$speechWeights/vosk-model-small-en-us-0.15/am/final.mdl")) {
    Expand-Archive -LiteralPath $voskArchive -DestinationPath $speechWeights
}

hf download rhasspy/piper-voices --revision 5ec43b186ef915a195d3ddbcfd9916e76c503c3f --include "en/en_US/lessac/medium/*" --local-dir $speechWeights
```

Reopen Settings after the speech inventory refreshes. The app checks installed models every 30 seconds. Each alternate engine runs on the CPU in a short-lived process. The process receives only the current text segment or PCM audio and frees its model memory when it exits. This trades warm-model latency for predictable idle memory use. Both engines use the same mono, 16 kHz audio contract.

Vosk publishes the small English model under Apache 2.0. Piper's implementation and each voice have separate terms. The pinned Lessac card identifies its training dataset and links to its conditions. [Vosk model list](https://alphacephei.com/vosk/models) · [Piper implementation](https://github.com/OHF-Voice/piper1-gpl) · [Lessac voice card](https://huggingface.co/rhasspy/piper-voices/blob/5ec43b186ef915a195d3ddbcfd9916e76c503c3f/en/en_US/lessac/medium/MODEL_CARD)
