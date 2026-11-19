#requires -Version 5.1
<#
.SYNOPSIS
Start LAB-03 then medisur-tools, displaying Azure instance status after each.
.DESCRIPTION
-Wait additionally polls SSH and unauthenticated health endpoints. It does not
retrieve credentials, start containers, or claim full pipeline readiness.
Known SSH host keys must already be verified in the operator's known_hosts.
#>
[CmdletBinding(SupportsShouldProcess=$true)]
param(
    [string]$Subscription = 'Azure for Students',
    [switch]$Wait,
    [ValidateRange(30,3600)][int]$TimeoutSeconds = 600,
    [string]$KeyPath = (Join-Path $PSScriptRoot '../../LAB-03_key.pem')
)
$ErrorActionPreference = 'Stop'
Get-Command az -ErrorAction Stop | Out-Null
function Invoke-AzJson {
    param([string[]]$Arguments)
    $output = & az @Arguments --only-show-errors --output json
    if ($LASTEXITCODE -ne 0) { throw "Azure CLI failed: $($Arguments[0..1] -join ' ')" }
    if ($output) { ($output -join "`n") | ConvertFrom-Json }
}
$account = Invoke-AzJson -Arguments @('account', 'show', '--subscription', $Subscription)
if ($account.state -ne 'Enabled') { throw 'Subscription is not enabled.' }
if ($Wait -and -not $WhatIfPreference) {
    Get-Command ssh -ErrorAction Stop | Out-Null
    $KeyPath = (Resolve-Path -LiteralPath $KeyPath).Path
}
$startedVms = @()
foreach ($vmName in @('LAB-03', 'medisur-tools')) {
    if (-not $PSCmdlet.ShouldProcess("$($account.name)/LAB-03/$vmName", 'Start VM')) { continue }
    Invoke-AzJson -Arguments @('vm', 'start', '--resource-group', 'LAB-03', '--name', $vmName, '--subscription', $account.id) | Out-Null
    $startedVms += $vmName
    Write-Host "$vmName Azure instance status (not service readiness):"
    Invoke-AzJson -Arguments @('vm', 'get-instance-view', '--resource-group', 'LAB-03', '--name', $vmName,
        '--query', 'instanceView.statuses', '--subscription', $account.id) | Format-Table | Out-Host
}
if (-not $Wait -or $WhatIfPreference) { return }
foreach ($vmName in $startedVms) {
    $address = Invoke-AzJson -Arguments @('vm', 'show', '--show-details', '--resource-group', 'LAB-03', '--name', $vmName,
        '--query', 'publicIps', '--subscription', $account.id)
    if ($address -notmatch '^\d{1,3}(\.\d{1,3}){3}$') { throw "No single IPv4 SSH address found for $vmName." }
    # Fixed commands only; no credentials or privileged service changes.
    $checks = if ($vmName -eq 'LAB-03') {
        @('curl -fsS --connect-timeout 3 --max-time 5 http://172.16.0.4:30081/health > /dev/null',
          'curl -fsS --connect-timeout 3 --max-time 5 http://172.16.0.4:30081/ready > /dev/null')
    } else {
        @('curl -fsS --connect-timeout 3 --max-time 5 http://127.0.0.1:8080/login > /dev/null',
          'curl -fsS --connect-timeout 3 --max-time 5 http://127.0.0.1:9000/api/system/status | python3 -c ''import json,sys; sys.exit(0 if json.load(sys.stdin).get("status") == "UP" else 1)''',
          'curl -fsS --connect-timeout 3 --max-time 5 http://127.0.0.1:3000/api/health | python3 -c ''import json,sys; sys.exit(0 if json.load(sys.stdin).get("database") == "ok" else 1)''',
          'curl -fsS --connect-timeout 3 --max-time 5 http://127.0.0.1:9090/-/ready > /dev/null')
    }
    $remoteCommand = 'set -e; set -o pipefail; ' + ($checks -join '; ')
    # Transport fixed health commands without Windows native-argument quote loss.
    $encodedChecks = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($remoteCommand))
    $remoteCommand = "printf %s $encodedChecks | base64 --decode | bash"
    $deadline = [datetime]::UtcNow.AddSeconds($TimeoutSeconds)
    $healthy = $false
    do {
        & ssh -F none -T -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=5 -o ConnectionAttempts=1 `
            -o ServerAliveInterval=5 -o ServerAliveCountMax=1 -o IdentitiesOnly=yes -i $KeyPath "username@$address" $remoteCommand
        $sshExit = $LASTEXITCODE
        if ($sshExit -eq 0) { $healthy = $true; break }
        if ([datetime]::UtcNow -lt $deadline) { Start-Sleep -Seconds 5 }
    } while ([datetime]::UtcNow -lt $deadline)
    if (-not $healthy) { throw "$vmName health checks timed out. VMs remain allocated; inspect SSH/NSG/services. Use stop-lab.ps1 to deallocate." }
    Write-Host "${vmName}: SSH and the listed HTTP health checks passed; builds/metrics/credentials were not validated."
}