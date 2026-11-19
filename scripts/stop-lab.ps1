#requires -Version 5.1
<#
.SYNOPSIS
Deallocate tools then cluster; never delete disks, IPs, NICs, or resources.
.DESCRIPTION
Attempts both VMs even if one fails, then reports failures. Persistent disks and
reserved public IPs can still incur charges. Running builds will be interrupted.
#>
[CmdletBinding(SupportsShouldProcess=$true)]
param([string]$Subscription = 'Azure for Students')
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
$failures = @()
foreach ($vmName in @('medisur-tools', 'LAB-03')) {
    if (-not $PSCmdlet.ShouldProcess("$($account.name)/LAB-03/$vmName", 'Deallocate VM (interrupts running work)')) { continue }
    try {
        Invoke-AzJson -Arguments @('vm', 'deallocate', '--resource-group', 'LAB-03', '--name', $vmName, '--subscription', $account.id) | Out-Null
        $statuses = @(Invoke-AzJson -Arguments @('vm', 'get-instance-view', '--resource-group', 'LAB-03', '--name', $vmName,
            '--query', 'instanceView.statuses', '--subscription', $account.id))
        Write-Host "$vmName Azure instance status:"
        $statuses | Format-Table | Out-Host
        if (-not ($statuses | Where-Object { $_.code -eq 'PowerState/deallocated' })) {
            throw 'Azure did not report PowerState/deallocated; deallocation is not confirmed.'
        }
    } catch {
        $failures += "${vmName}: $($_.Exception.Message)"
        Write-Warning $failures[-1]
    }
}
Write-Host 'No resources deleted. Disks/public IPs can still incur charges after deallocation.'
if ($failures.Count) { throw ($failures -join "`n") }