param(
    [string]$ProjectId = "project-871ad35a-5a6f-40a4-8f0"
)

$ErrorActionPreference = "Stop"
$gcloud = Join-Path $env:LOCALAPPDATA "Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd"
if (-not (Test-Path -LiteralPath $gcloud)) {
    throw "Google Cloud SDK was not found."
}

function Add-HiddenSecretVersion {
    param(
        [string]$SecretName,
        [string]$Prompt
    )

    $secure = Read-Host -Prompt $Prompt -AsSecureString
    $credential = [System.Management.Automation.PSCredential]::new("secret", $secure)
    $plain = $credential.GetNetworkCredential().Password
    if ([string]::IsNullOrWhiteSpace($plain)) {
        throw "$SecretName cannot be empty."
    }

    try {
        $plain | & $gcloud secrets versions add $SecretName --data-file=- --project=$ProjectId
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to upload $SecretName."
        }
    }
    finally {
        $plain = $null
        $credential = $null
        $secure.Dispose()
    }
}

Write-Host "The values stay hidden and are sent directly to Google Secret Manager." -ForegroundColor Cyan
Add-HiddenSecretVersion -SecretName "whu-deepseek-api-key" -Prompt "Paste DeepSeek API key"
Add-HiddenSecretVersion -SecretName "whu-feishu-webhook" -Prompt "Paste Feishu webhook URL"
Write-Host ""
Write-Host "SUCCESS: both secret versions were uploaded." -ForegroundColor Green
Read-Host "Press Enter to close"
