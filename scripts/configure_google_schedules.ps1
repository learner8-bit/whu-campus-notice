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
$roleId = "whuNoticeJobRunner"
$roleResource = "projects/$Project/roles/$roleId"
$existingRoles = @(
    & $gcloud iam roles list --project=$Project --format="value(name.basename())"
)
if ($LASTEXITCODE -ne 0) {
    throw "Failed to list project IAM roles."
}
if ($existingRoles -contains $roleId) {
    & $gcloud iam roles update $roleId --project=$Project `
        --title="WHU Notice Job Runner" `
        --permissions=run.jobs.run,run.jobs.runWithOverrides --stage=GA
}
else {
    & $gcloud iam roles create $roleId --project=$Project `
        --title="WHU Notice Job Runner" `
        --permissions=run.jobs.run,run.jobs.runWithOverrides --stage=GA
}
if ($LASTEXITCODE -ne 0) {
    throw "Failed to configure the least-privilege scheduler role."
}
& $gcloud run jobs add-iam-policy-binding $Job --project=$Project --region=$Region `
    --member="serviceAccount:$SchedulerServiceAccount" --role=$roleResource
if ($LASTEXITCODE -ne 0) {
    throw "Failed to grant the scheduler role on $Job."
}

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
    $name = [string]$item.Name
    $cron = [string]$item.Cron
    $phase = [string]$item.Phase
    $body = @{
        overrides = @{
            containerOverrides = @(
                @{ env = @( @{ name = "RUN_PHASE"; value = $phase } ) }
            )
        }
    } | ConvertTo-Json -Depth 8 -Compress
    $bodyFile = [System.IO.Path]::GetTempFileName()
    try {
        [System.IO.File]::WriteAllText(
            $bodyFile,
            $body,
            [System.Text.UTF8Encoding]::new($false)
        )
        if ($existingJobs -contains $name) {
            & $gcloud scheduler jobs update http $name `
                --project=$Project --location=$Region --schedule=$cron `
                --time-zone=Asia/Shanghai --uri=$uri --http-method=POST `
                --oauth-service-account-email=$SchedulerServiceAccount `
                --oauth-token-scope=https://www.googleapis.com/auth/cloud-platform `
                --update-headers=Content-Type=application/json `
                --message-body-from-file=$bodyFile --attempt-deadline=180s
        }
        else {
            & $gcloud scheduler jobs create http $name `
                --project=$Project --location=$Region --schedule=$cron `
                --time-zone=Asia/Shanghai --uri=$uri --http-method=POST `
                --oauth-service-account-email=$SchedulerServiceAccount `
                --oauth-token-scope=https://www.googleapis.com/auth/cloud-platform `
                --headers=Content-Type=application/json `
                --message-body-from-file=$bodyFile --attempt-deadline=180s
        }
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to configure $name."
        }
    }
    finally {
        Remove-Item -LiteralPath $bodyFile -Force -ErrorAction SilentlyContinue
    }
}

Write-Host "SUCCESS: staged Asia/Shanghai schedules are configured." -ForegroundColor Green
