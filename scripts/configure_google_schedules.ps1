param(
    [string]$Project = "project-871ad35a-5a6f-40a4-8f0",
    [string]$Region = "us-west1",
    [string]$Job = "whu-campus-notice",
    [string]$SchedulerServiceAccount = "whu-notice-scheduler@project-871ad35a-5a6f-40a4-8f0.iam.gserviceaccount.com"
)

$ErrorActionPreference = "Stop"
$gcloud = Join-Path $env:LOCALAPPDATA "Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd"
if (-not (Test-Path -LiteralPath $gcloud)) {
    throw "Google Cloud SDK was not found."
}

$uri = "https://run.googleapis.com/v2/projects/$Project/locations/$Region/jobs/${Job}:run"
$schedules = @(
    @{ Name = "whu-notice-wechat-primary"; Cron = "50 20 * * *"; Phase = "wechat-sync" },
    @{ Name = "whu-notice-wechat-retry"; Cron = "20 21 * * *"; Phase = "wechat-sync" },
    @{ Name = "whu-notice-prepare"; Cron = "35 21 * * *"; Phase = "prepare" },
    @{ Name = "whu-notice-freeze"; Cron = "50 21 * * *"; Phase = "freeze" },
    @{ Name = "whu-notice-main"; Cron = "0 22 * * *"; Phase = "send" },
    @{ Name = "whu-notice-retry-1"; Cron = "11 22 * * *"; Phase = "send" },
    @{ Name = "whu-notice-retry-2"; Cron = "41 22 * * *"; Phase = "send" },
    @{ Name = "whu-notice-retry-3"; Cron = "11 23 * * *"; Phase = "send" }
)

$existingJobs = @(
    & $gcloud scheduler jobs list --project=$Project --location=$Region `
        --format="value(name.basename())"
)
if ($LASTEXITCODE -ne 0) {
    throw "Failed to list existing Cloud Scheduler jobs."
}

foreach ($item in $schedules) {
    $body = @{
        overrides = @{
            containerOverrides = @(
                @{ env = @( @{ name = "RUN_PHASE"; value = $item.Phase } ) }
            )
        }
    } | ConvertTo-Json -Depth 8 -Compress

    if ($existingJobs -contains $item.Name) {
        & $gcloud scheduler jobs update http $item.Name `
            --project=$Project --location=$Region --schedule=$item.Cron `
            --time-zone=Asia/Shanghai --uri=$uri --http-method=POST `
            --oauth-service-account-email=$SchedulerServiceAccount `
            --oauth-token-scope=https://www.googleapis.com/auth/cloud-platform `
            --headers=Content-Type=application/json --message-body=$body `
            --attempt-deadline=180s
    }
    else {
        & $gcloud scheduler jobs create http $item.Name `
            --project=$Project --location=$Region --schedule=$item.Cron `
            --time-zone=Asia/Shanghai --uri=$uri --http-method=POST `
            --oauth-service-account-email=$SchedulerServiceAccount `
            --oauth-token-scope=https://www.googleapis.com/auth/cloud-platform `
            --headers=Content-Type=application/json --message-body=$body `
            --attempt-deadline=180s
    }
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to configure $($item.Name)."
    }
}

Write-Host "SUCCESS: staged Asia/Shanghai schedules are configured." -ForegroundColor Green
