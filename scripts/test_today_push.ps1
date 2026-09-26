param(
    [string]$Project = "project-871ad35a-5a6f-40a4-8f0",
    [string]$Region = "us-west1",
    [string]$Job = "whu-campus-notice-test"
)

$ErrorActionPreference = "Stop"

function Write-Step([string]$Message) {
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Stop-WithMessage([string]$Message) {
    Write-Host ""
    Write-Host "FAILED: $Message" -ForegroundColor Red
    exit 1
}

Write-Host "WHU Campus Notice: isolated cloud test" -ForegroundColor Green
Write-Host "This script never reads or prints the DeepSeek key or Feishu webhook."
Write-Host "Test runs never update the production database or delivery marker."

$cloudSdk = (Get-Command gcloud.cmd -ErrorAction SilentlyContinue).Source
if (-not $cloudSdk) {
    $cloudSdk = Join-Path $env:LOCALAPPDATA "Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd"
}
if (-not (Test-Path -LiteralPath $cloudSdk)) {
    Stop-WithMessage "Google Cloud CLI was not found."
}

Write-Step "Starting isolated Google Cloud collection and Feishu delivery"
& $cloudSdk run jobs execute $Job --region=$Region --project=$Project --wait
if ($LASTEXITCODE -ne 0) {
    Stop-WithMessage "The Google Cloud test job failed. Send the error above to Codex."
}

Write-Host ""
Write-Host "SUCCESS: the isolated cloud test completed. Check Feishu now." -ForegroundColor Green
Write-Host "Production delivery status was not changed." -ForegroundColor Yellow