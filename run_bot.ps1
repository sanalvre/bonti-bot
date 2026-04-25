param(
    [string]$Token,
    [string]$Model = "large-v3-turbo",
    [int]$MaxAudioSeconds = 240,
    [int]$MaxAttachmentMb = 25,
    [int]$GlobalConcurrency = 1,
    [string]$LogLevel = "INFO",
    [string]$FfmpegPath = "ffmpeg"
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonExe = Join-Path $projectRoot ".venv\\Scripts\\python.exe"

if (-not (Test-Path $pythonExe)) {
    throw "Virtual environment not found. Expected: $pythonExe"
}

if (-not $Token) {
    $Token = $env:DISCORD_BOT_TOKEN
}

if (-not $Token) {
    $secureToken = Read-Host "Paste your Discord bot token" -AsSecureString
    $bstr = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureToken)
    try {
        $Token = [System.Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    }
    finally {
        [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    }
}

if (-not $Token) {
    throw "A Discord bot token is required."
}

$env:DISCORD_BOT_TOKEN = $Token
$env:TRANSCRIBE_MODEL = $Model
$env:MAX_AUDIO_SECONDS = "$MaxAudioSeconds"
$env:MAX_ATTACHMENT_MB = "$MaxAttachmentMb"
$env:GLOBAL_CONCURRENCY = "$GlobalConcurrency"
$env:LOG_LEVEL = $LogLevel
$env:FFMPEG_PATH = $FfmpegPath

Push-Location $projectRoot
try {
    & $pythonExe -m src.transcriber_bot
}
finally {
    Pop-Location
}
