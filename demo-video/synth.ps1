param([Parameter(Mandatory)][string]$TextFile, [Parameter(Mandatory)][string]$Out, [string]$Voice = "Microsoft Zira Desktop", [int]$Rate = -1)
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$s.SelectVoice($Voice)
$s.Rate = $Rate
$s.SetOutputToWaveFile($Out)
$s.Speak([System.IO.File]::ReadAllText($TextFile, [System.Text.Encoding]::UTF8))
$s.Dispose()
