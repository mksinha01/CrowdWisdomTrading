Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$synth.SetOutputToWaveFile("scratch\test_sapi.wav")
$synth.Speak("Too many voices. Every day, thousands of traders post their predictions.")
$synth.Dispose()
Write-Host "SAPI audio created: $(Test-Path 'scratch\test_sapi.wav')"
