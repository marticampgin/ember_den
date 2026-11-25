$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)

try {
    Add-Type -AssemblyName System.Speech
    $request = [Console]::In.ReadToEnd() | ConvertFrom-Json
    $format = [System.Speech.AudioFormat.SpeechAudioFormatInfo]::new(
        16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,
        [System.Speech.AudioFormat.AudioChannel]::Mono)
    $result = @{}
    if ($request.operation -in 'status','transcribe') {
        try {
            $english = @([System.Speech.Recognition.SpeechRecognitionEngine]::InstalledRecognizers() |
                Where-Object { $_.Culture.TwoLetterISOLanguageName -eq 'en' })
        } catch {
            if ($request.operation -ne 'status') { throw }
            $english = @()
        }
        $result.recognizers = @($english | ForEach-Object { $_.Culture.Name })
        $result.recognizer_options = @($english | ForEach-Object {
            @{ id = $_.Id; name = $_.Description; culture = $_.Culture.Name }
        })
    }
    if ($request.operation -in 'status','synthesize') {
        try {
            $synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
            $voices = @($synth.GetInstalledVoices() | Where-Object {
                $_.Enabled -and $_.VoiceInfo.Culture.TwoLetterISOLanguageName -eq 'en'
            } | ForEach-Object { $_.VoiceInfo.Name })
        } catch {
            if ($request.operation -ne 'status') { throw }
            $voices = @()
        }
        $result.voices = $voices
        if ($voices.Count) {
            $defaultVoice = $synth.Voice.Name
            if ($voices -notcontains $defaultVoice) { $defaultVoice = $voices[0] }
        }
        $result.default_voice = $defaultVoice
    }
    if ($request.operation -eq 'transcribe') {
        if (!$english.Count) {
            throw 'recognizer-unavailable'
        }
        $engine = $english | Where-Object { $_.Id -eq $request.recognizer } | Select-Object -First 1
        if (!$engine) {
            $engine = $english | Where-Object { $_.Culture.Name -eq $request.recognizer } | Select-Object -First 1
        }
        if (!$engine) { $engine = $english[0] }
        $recognizer = [System.Speech.Recognition.SpeechRecognitionEngine]::new($engine)
        $recognizer.LoadGrammar([System.Speech.Recognition.DictationGrammar]::new())
        $bytes = [Convert]::FromBase64String($request.pcm)
        $memory = [IO.MemoryStream]::new($bytes, $false)
        $recognizer.SetInputToAudioStream($memory, $format)
        # Multiple mode runs to the end of the clip, including pauses between phrases.
        Register-ObjectEvent $recognizer SpeechRecognized -SourceIdentifier DenPhrase | Out-Null
        Register-ObjectEvent $recognizer RecognizeCompleted -SourceIdentifier DenComplete | Out-Null
        $recognizer.RecognizeAsync([System.Speech.Recognition.RecognizeMode]::Multiple)
        $finished = Wait-Event -SourceIdentifier DenComplete -Timeout 24
        if (!$finished -or $finished.SourceEventArgs.Error) { throw 'recognition-incomplete' }
        $phrases = @(Get-Event -SourceIdentifier DenPhrase -ErrorAction SilentlyContinue |
            ForEach-Object { $_.SourceEventArgs.Result.Text })
        $result = @{ text = $phrases -join ' '; language = $engine.Culture.Name; recognizer = $engine.Id }
    } elseif ($request.operation -eq 'synthesize') {
        if (!$voices.Count) { throw 'voice-unavailable' }
        $name = $request.voice
        if (!$name -or $voices -notcontains $name) { $name = $defaultVoice }
        $synth.SelectVoice($name)
        $memory = New-Object IO.MemoryStream
        $synth.SetOutputToAudioStream($memory, $format)
        $synth.Speak([string]$request.text)
        $synth.SetOutputToNull()
        $result = @{ pcm = [Convert]::ToBase64String($memory.ToArray()); voice = $name }
    }
} catch {
    $message = switch ($_.Exception.Message) {
        'recognizer-unavailable' { 'Install English speech recognition in Windows language settings.' }
        'voice-unavailable' { 'Choose an installed English voice in Settings.' }
        default { 'Windows could not process speech. Check your English speech installation.' }
    }
    $result = @{ error = $message }
} finally {
    if ($recognizer) { $recognizer.Dispose() }
    if ($synth) { $synth.Dispose() }
    if ($memory) { $memory.Dispose() }
}
$result | ConvertTo-Json -Depth 3 -Compress


