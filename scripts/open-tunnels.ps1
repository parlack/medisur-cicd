#requires -Version 5.1
<#
.SYNOPSIS
Open five loopback-only SSH tunnels in the foreground. Ctrl+C closes this session.
.DESCRIPTION
Fails if any requested local port is occupied; never terminates existing tunnels
or other processes. No Azure calls or secrets retrieval. Verify the SSH host key
independently and enroll it in known_hosts before using this script.
#>
[CmdletBinding()]
param(
    [string]$KeyPath = (Join-Path $PSScriptRoot '../../LAB-03_key.pem'),
    [ValidatePattern('^[a-zA-Z0-9][a-zA-Z0-9.-]*$')]
    [string]$ToolsHost = 'medisur-sergio-cicd.eastus.cloudapp.azure.com'
)
$ErrorActionPreference = 'Stop'
Get-Command ssh -ErrorAction Stop | Out-Null
$KeyPath = (Resolve-Path -LiteralPath $KeyPath).Path
$forwards = @(
    @{Name='Jenkins'; Local=18080; Target='127.0.0.1:8080'},
    @{Name='SonarQube'; Local=19000; Target='127.0.0.1:9000'},
    @{Name='Grafana'; Local=13000; Target='127.0.0.1:3000'},
    @{Name='Prometheus'; Local=19090; Target='127.0.0.1:9090'},
    @{Name='App'; Local=18000; Target='172.16.0.4:30081'}
)
# Hold all probe sockets until every port has been checked. SSH subsequently
# checks binds again to cover a race after these probes are released.
$listeners = @()
try {
    foreach ($forward in $forwards) {
        $listener = New-Object Net.Sockets.TcpListener -ArgumentList ([Net.IPAddress]::Loopback), $forward.Local
        $listeners += $listener
        $listener.Server.ExclusiveAddressUse = $true
        try { $listener.Start() } catch {
            throw "Cannot bind 127.0.0.1:$($forward.Local) ($($forward.Name)); existing processes/tunnels were preserved. $($_.Exception.Message)"
        }
    }
} finally {
    foreach ($listener in $listeners) { $listener.Stop() }
}
$arguments = @('-F', 'none', '-N', '-T', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
    '-o', 'ExitOnForwardFailure=yes', '-o', 'ControlMaster=no', '-o', 'ControlPath=none',
    '-o', 'IdentitiesOnly=yes', '-o', 'ConnectTimeout=10', '-o', 'ServerAliveInterval=30',
    '-o', 'ServerAliveCountMax=3', '-i', $KeyPath)
foreach ($forward in $forwards) {
    $arguments += @('-L', "127.0.0.1:$($forward.Local):$($forward.Target)")
    Write-Host "$($forward.Name): http://127.0.0.1:$($forward.Local)"
}
$arguments += "username@$ToolsHost"
Write-Host 'Opening foreground SSH session; URLs are mappings, not a readiness claim. Ctrl+C closes only this session.'
& ssh @arguments
if ($LASTEXITCODE -ne 0) { throw "SSH tunnel session exited with code $LASTEXITCODE. No existing processes were stopped." }