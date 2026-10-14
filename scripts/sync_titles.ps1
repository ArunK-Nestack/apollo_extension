# PowerShell launcher for Freshsales to MySQL Job Title Synchronization
param (
    [int]$BatchSize = 2500,
    [int]$TelegramInterval = 300,
    [int]$Limit = 0,
    [switch]$NoTelegram,
    [switch]$Reset,
    [switch]$DryRun
)

$argsList = @("scripts/sync_job_titles_to_mysql.py", "--batch-size", $BatchSize, "--telegram-interval", $TelegramInterval)

if ($Reset) {
    $argsList += "--reset"
}

if ($DryRun) {
    $argsList += "--dry-run"
}

if ($NoTelegram) {
    $argsList += "--no-telegram"
}

if ($Limit -gt 0) {
    $argsList += @("--limit", $Limit)
}

Write-Host ">>> Launching Job Title Synchronization (Telegram 5-Min Reports: Active)..." -ForegroundColor Cyan
python @argsList
