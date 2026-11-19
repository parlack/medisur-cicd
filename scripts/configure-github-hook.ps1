param(
    [string]$KeyPath = (Join-Path (Split-Path $PSScriptRoot -Parent | Split-Path -Parent) 'LAB-03_key.pem')
)
$ErrorActionPreference = 'Stop'
$github = 'C:\Program Files\GitHub CLI\gh.exe'
$secret = & ssh -o BatchMode=yes -i $KeyPath username@20.127.28.221 'sudo cat /etc/medisur/secrets/github_webhook_secret'
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($secret)) { throw 'Cannot read webhook credential securely' }
$payload = @{
    name = 'web'; active = $true; events = @('push', 'pull_request')
    config = @{
        url = 'https://medisur-sergio-cicd.eastus.cloudapp.azure.com/github-webhook/'
        content_type = 'json'; insecure_ssl = '0'; secret = $secret.Trim()
    }
} | ConvertTo-Json -Depth 5 -Compress
try {
    $hooks = & $github api repos/SergioMultimedios/medisur-cicd/hooks | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0) { throw 'Cannot list hooks' }
    $existing = $hooks | Where-Object { $_.config.url -eq 'https://medisur-sergio-cicd.eastus.cloudapp.azure.com/github-webhook/' } | Select-Object -First 1
    if ($existing) {
        $payload | & $github api "repos/SergioMultimedios/medisur-cicd/hooks/$($existing.id)" -X PATCH --input - --jq '{id: .id, active: .active, last_response: .last_response}'
    } else {
        $payload | & $github api repos/SergioMultimedios/medisur-cicd/hooks -X POST --input - --jq '{id: .id, active: .active, last_response: .last_response}'
    }
    if ($LASTEXITCODE -ne 0) { throw 'Hook configuration failed' }
} finally {
    $secret = $null
    $payload = $null
}