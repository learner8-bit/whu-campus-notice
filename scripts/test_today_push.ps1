param(
    [string]$Repository = "learner8-bit/whu-campus-notice",
    [string]$Workflow = "daily-notice-digest.yml"
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

Write-Host "WHU Campus Notice: test today's cloud digest" -ForegroundColor Green
Write-Host "This script never reads or prints the DeepSeek key or Feishu webhook."

if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    Stop-WithMessage "GitHub CLI (gh) is not installed. Install it from https://cli.github.com/"
}

Write-Step "Checking GitHub login"
& gh auth status --hostname github.com
if ($LASTEXITCODE -ne 0) {
    Write-Host "Complete GitHub login in the browser, then run this file again." -ForegroundColor Yellow
    & gh auth login --hostname github.com --web --git-protocol https
    if ($LASTEXITCODE -ne 0) {
        Stop-WithMessage "GitHub login was not completed."
    }
}

$startedAt = (Get-Date).ToUniversalTime().AddMinutes(-2)

Write-Step "Starting cloud collection, DeepSeek analysis, and Feishu delivery"
& gh workflow run $Workflow --repo $Repository -f force_send=true
if ($LASTEXITCODE -ne 0) {
    Stop-WithMessage "Could not start the workflow. Check your network and repository access."
}

Write-Host "The job was submitted. Waiting for GitHub Actions..."
$run = $null
for ($attempt = 1; $attempt -le 15; $attempt++) {
    Start-Sleep -Seconds 2

    $raw = & gh run list --repo $Repository --workflow $Workflow --event workflow_dispatch --limit 5 --json databaseId,createdAt,status,conclusion,url
    if ($LASTEXITCODE -ne 0) {
        continue
    }

    $runs = $raw | ConvertFrom-Json
    $run = $runs |
        Where-Object { ([datetime]$_.createdAt).ToUniversalTime() -ge $startedAt } |
        Sort-Object { [datetime]$_.createdAt } -Descending |
        Select-Object -First 1

    if ($null -ne $run) {
        break
    }
}

if ($null -eq $run) {
    Stop-WithMessage "The workflow started, but its run record was not found yet. Open the repository Actions page."
}

Write-Host "Run URL: $($run.url)" -ForegroundColor DarkGray
Write-Step "Waiting for the cloud job to finish"
& gh run watch $run.databaseId --repo $Repository --exit-status
$watchExit = $LASTEXITCODE

if ($watchExit -ne 0) {
    Write-Host "The local GitHub connection was interrupted. Rechecking the same cloud run..." -ForegroundColor Yellow
    $conclusion = ""
    for ($attempt = 1; $attempt -le 60; $attempt++) {
        $stateRaw = & gh run view $run.databaseId --repo $Repository --json status,conclusion 2>$null
        if ($LASTEXITCODE -eq 0) {
            $state = $stateRaw | ConvertFrom-Json
            if ($state.status -eq "completed") {
                $conclusion = $state.conclusion
                break
            }
        }
        Start-Sleep -Seconds 5
    }
    if ($conclusion -eq "success") {
        $watchExit = 0
        Write-Host "The cloud run succeeded; only the local status connection was interrupted." -ForegroundColor Green
    }
}

if ($watchExit -ne 0) {
    Write-Host ""
    Write-Host "The cloud job failed. Failed-step logs follow:" -ForegroundColor Red
    & gh run view $run.databaseId --repo $Repository --log-failed
    Stop-WithMessage "Keep the error above and send it to Codex for repair."
}

Write-Host ""
Write-Host "SUCCESS: the cloud test completed. Check Feishu now." -ForegroundColor Green
Write-Host "Test mode bypasses digest deduplication, so Feishu should receive a message." -ForegroundColor Yellow
Write-Host "Run URL: $($run.url)"
