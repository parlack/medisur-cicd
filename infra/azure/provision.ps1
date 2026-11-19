#requires -Version 5.1
<#
.SYNOPSIS
Read-only Azure inventory and plan; -Create creates missing lab resources only.
.DESCRIPTION
Requires an existing az login. Never selects a different CLI default account,
replaces existing resources, installs software, or changes LAB-03 OS/disks.
Existing conflicting configuration stops execution for manual review. Operations
are not transactional: rerun after reviewing a failure; never delete to recover.
Cost Management returns reported actual charges, with billing latency, not quotes.
#>
[CmdletBinding()]
param(
    [switch]$Create,
    [string]$Subscription = 'Azure for Students',
    [string]$KeyPath = (Join-Path $PSScriptRoot '../../../LAB-03_key.pem'),
    [ValidatePattern('^[a-zA-Z0-9_-]+$')][string]$BudgetName = 'medisur-monthly-50'
)
$ErrorActionPreference = 'Stop'
Get-Command az -ErrorAction Stop | Out-Null
$resourceGroup = 'LAB-03'
$location = 'eastus'
$toolsName = 'medisur-tools'
$dnsLabel = 'medisur-sergio-cicd'
$size = 'Standard_D4s_v4'
$image = 'Canonical:0001-com-ubuntu-server-jammy:22_04-lts-gen2:latest'
$budgetPath = Join-Path $PSScriptRoot 'budget.json'
$costPath = Join-Path $PSScriptRoot 'cost-query.json'
$budgetManifest = Get-Content -LiteralPath $budgetPath -Raw | ConvertFrom-Json
$costManifest = Get-Content -LiteralPath $costPath -Raw | ConvertFrom-Json
if ($costManifest.type -ne 'ActualCost') { throw 'Cost query must request ActualCost.' }

function Invoke-AzJson {
    param([string[]]$Arguments)
    $output = & az @Arguments --only-show-errors --output json
    if ($LASTEXITCODE -ne 0) { throw "Azure CLI failed: $($Arguments[0..1] -join ' ')" }
    if ($output) { return (($output -join "`n") | ConvertFrom-Json) }
}
function Get-ResourceById {
    param([string]$Id)
    Invoke-AzJson -Arguments @('resource', 'show', '--ids', $Id, '--subscription', $script:subscriptionId)
}
function Assert-Equal {
    param($Actual, $Expected, [string]$Label)
    if ([string]$Actual -ine [string]$Expected) {
        throw "Existing $Label differs (actual '$Actual', expected '$Expected'). Not overwritten."
    }
}
function Get-Nic {
    param($Vm)
    $references = @($Vm.networkProfile.networkInterfaces)
    if ($references.Count -ne 1) { throw "Review multiple NICs on $($Vm.name) manually." }
    Invoke-AzJson -Arguments @('network', 'nic', 'show', '--ids', $references[0].id, '--subscription', $script:subscriptionId)
}

$account = Invoke-AzJson -Arguments @('account', 'show', '--subscription', $Subscription)
Assert-Equal $account.state 'Enabled' 'subscription state'
$subscriptionId = $account.id
Write-Host "Account: $($account.name); subscription: $subscriptionId; tenant: $($account.tenantId)"
Write-Host 'No account set, login, or resource-group creation is performed.'
$group = Invoke-AzJson -Arguments @('group', 'show', '--name', $resourceGroup, '--subscription', $subscriptionId)
Assert-Equal $group.location $location 'resource-group location'
$vms = @(Invoke-AzJson -Arguments @('vm', 'list', '--resource-group', $resourceGroup, '--subscription', $subscriptionId))
$cluster = $vms | Where-Object { $_.name -eq 'LAB-03' }
if (-not $cluster) { throw 'Preexisting LAB-03 VM is required; it will never be created.' }
Assert-Equal $cluster.location $location 'cluster location'
Assert-Equal $cluster.hardwareProfile.vmSize 'Standard_D2as_v4' 'cluster size'
$clusterNic = Get-Nic $cluster
Assert-Equal $clusterNic.ipConfigurations[0].privateIPAddress '172.16.0.4' 'cluster private IP'
Assert-Equal $clusterNic.ipConfigurations[0].privateIPAllocationMethod 'Static' 'cluster private IP allocation'
$subnet = Invoke-AzJson -Arguments @('network', 'vnet', 'subnet', 'show', '--resource-group', $resourceGroup,
    '--vnet-name', 'vnet-eastus-1', '--name', 'snet-eastus-1', '--subscription', $subscriptionId)
Assert-Equal $clusterNic.ipConfigurations[0].subnet.id $subnet.id 'cluster subnet'
Write-Host "PRESERVE cluster OS image: $($cluster.storageProfile.imageReference | ConvertTo-Json -Compress)"
Write-Host "PRESERVE cluster OS disk: $($cluster.storageProfile.osDisk.name) ($($cluster.storageProfile.osDisk.diskSizeGb) GB)"

# Availability and quota are evidence at query time, not a capacity reservation.
$skus = @(Invoke-AzJson -Arguments @('vm', 'list-skus', '--location', $location, '--size', $size,
    '--resource-type', 'virtualMachines', '--all', '--subscription', $subscriptionId,
    '--query', '[].{name:name,resourceType:resourceType,family:family,restrictions:restrictions}'))
$sku = $skus | Where-Object { $_.name -eq $size -and $_.resourceType -eq 'virtualMachines' }
$usage = @(Invoke-AzJson -Arguments @('vm', 'list-usage', '--location', $location, '--subscription', $subscriptionId))
Write-Host 'SKU availability/restrictions (not a guarantee of allocation):'
$sku | ConvertTo-Json -Depth 15 | Write-Host
Write-Host 'Regional compute quota/usage:'
$usage | Select-Object @{n='Quota';e={$_.name.value}}, currentValue, limit | Format-Table | Out-Host
Write-Host 'Reported month-to-date ActualCost; currency/columns are returned by Azure. No hourly estimate.'
try {
    $cost = Invoke-AzJson -Arguments @('rest', '--method', 'post', '--url',
        "https://management.azure.com/subscriptions/$subscriptionId/providers/Microsoft.CostManagement/query?api-version=2023-03-01",
        '--body', "@$costPath", '--subscription', $subscriptionId)
    $cost.properties | ConvertTo-Json -Depth 15 | Write-Host
    if (-not $cost.properties.rows) { Write-Warning 'No reported cost rows; this does not establish zero cost.' }
} catch { Write-Warning "Actual costs unavailable; no estimate substituted. $($_.Exception.Message)" }

$tools = $vms | Where-Object { $_.name -eq $toolsName }
$nsgs = @(Invoke-AzJson -Arguments @('network', 'nsg', 'list', '--resource-group', $resourceGroup, '--subscription', $subscriptionId))
$toolsNsg = $nsgs | Where-Object { $_.name -eq 'medisur-tools-nsg' }
$publicIps = @(Invoke-AzJson -Arguments @('network', 'public-ip', 'list', '--resource-group', $resourceGroup, '--subscription', $subscriptionId))
$nics = @(Invoke-AzJson -Arguments @('network', 'nic', 'list', '--resource-group', $resourceGroup, '--subscription', $subscriptionId))
if ($tools) {
    Assert-Equal $tools.location $location 'tools location'
    Assert-Equal $tools.hardwareProfile.vmSize $size 'tools size'
    Assert-Equal $tools.osProfile.adminUsername 'username' 'tools SSH user'
    Assert-Equal $tools.storageProfile.imageReference.publisher 'Canonical' 'tools image publisher'
    Assert-Equal $tools.storageProfile.imageReference.offer '0001-com-ubuntu-server-jammy' 'tools image offer'
    Assert-Equal $tools.storageProfile.imageReference.sku '22_04-lts-gen2' 'tools image SKU'
    $disk = Get-ResourceById $tools.storageProfile.osDisk.managedDisk.id
    Assert-Equal $disk.sku.name 'StandardSSD_LRS' 'tools disk SKU'
    Assert-Equal $disk.properties.diskSizeGB 64 'tools disk size'
    $toolsNic = Get-Nic $tools
} else {
    $toolsNic = $nics | Where-Object { $_.name -eq 'medisur-tools-nic' }
    if ($toolsNic.virtualMachine.id) { throw 'Existing tools NIC is attached to another VM; not reused.' }
    $disks = @(Invoke-AzJson -Arguments @('disk', 'list', '--resource-group', $resourceGroup, '--subscription', $subscriptionId))
    if ($disks | Where-Object { $_.name -eq 'medisur-tools-osdisk' }) {
        throw 'Tools OS disk already exists without the expected VM. Manual recovery required; disk is not reused or overwritten.'
    }
}
if ($toolsNic) {
    if (@($toolsNic.ipConfigurations).Count -ne 1) { throw 'Review multiple tools IP configurations manually.' }
    Assert-Equal $toolsNic.ipConfigurations[0].privateIPAddress '172.16.0.5' 'tools private IP'
    Assert-Equal $toolsNic.ipConfigurations[0].privateIPAllocationMethod 'Static' 'tools private IP allocation'
    Assert-Equal $toolsNic.ipConfigurations[0].subnet.id $subnet.id 'tools subnet'
    if (-not $toolsNsg) { throw 'Existing tools NIC requires the expected NSG; no reassociation performed.' }
    Assert-Equal $toolsNic.networkSecurityGroup.id $toolsNsg.id 'tools NIC NSG'
    $toolsPublicIp = $publicIps | Where-Object { $_.id -eq $toolsNic.ipConfigurations[0].publicIPAddress.id }
    if (-not $toolsPublicIp) { throw 'Existing tools NIC has no recognized public IP. Review manually.' }
} else {
    $toolsPublicIp = $publicIps | Where-Object { $_.name -eq 'medisur-tools-ip' }
    if ($toolsPublicIp.ipConfiguration.id) { throw 'Tools public IP is already attached elsewhere; not reused.' }
}
if ($toolsPublicIp) {
    Assert-Equal $toolsPublicIp.location $location 'tools public IP location'
    Assert-Equal $toolsPublicIp.sku.name 'Standard' 'tools public IP SKU'
    Assert-Equal $toolsPublicIp.publicIPAllocationMethod 'Static' 'tools public IP allocation'
    if ($toolsPublicIp.dnsSettings.domainNameLabel) {
        Assert-Equal $toolsPublicIp.dnsSettings.domainNameLabel $dnsLabel 'tools DNS label'
    }
    Write-Host "Reuse actual tools public IP: $($toolsPublicIp.ipAddress); documented deployment: 20.127.28.221"
}
$clusterPublicIp = $publicIps | Where-Object { $_.id -eq $clusterNic.ipConfigurations[0].publicIPAddress.id }
Write-Host "Cluster public IP: $($clusterPublicIp.ipAddress); documented deployment: 52.151.192.168"

# ipify is contacted only when this script is run, never during static validation.
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$operatorIp = ([string](Invoke-RestMethod -Uri 'https://api.ipify.org' -TimeoutSec 15)).Trim()
$parsedIp = $null
if (-not [Net.IPAddress]::TryParse($operatorIp, [ref]$parsedIp) -or
    $parsedIp.AddressFamily -ne [Net.Sockets.AddressFamily]::InterNetwork) { throw 'ipify did not return an IPv4 address.' }
$operatorCidr = "$operatorIp/32"
Write-Host "SSH operator source: $operatorCidr. Changed IP/rule conflicts require manual review, not overwrite."

$rulePlan = @()
function Add-RulePlan {
    param([string]$NsgId, [string]$Name, [int]$Priority, [string]$Source, [string]$Destination,
        [string[]]$Ports, [string]$Access)
    $script:rulePlan += [pscustomobject]@{NsgId=$NsgId; Name=$Name; Priority=$Priority; Source=$Source;
        Destination=$Destination; Ports=$Ports; Access=$Access}
}
$toolsNsgId = "$($group.id)/providers/Microsoft.Network/networkSecurityGroups/medisur-tools-nsg"
$clusterNsgId = $clusterNic.networkSecurityGroup.id
if (-not $clusterNsgId) { throw 'Cluster NIC NSG absent; review network controls manually. No attachment changes are automated.' }
foreach ($entry in @(@{Id=$clusterNsgId; Ip='172.16.0.4'}, @{Id=$toolsNsgId; Ip='172.16.0.5'})) {
    $suffix = $entry.Ip.Split('.')[-1]
    Add-RulePlan $entry.Id "medisur-operator-ssh-$suffix" 100 $operatorCidr $entry.Ip @('22') 'Allow'
    Add-RulePlan $entry.Id "medisur-deny-internet-ssh-$suffix" 101 'Internet' $entry.Ip @('22') 'Deny'
    Add-RulePlan $entry.Id "medisur-deny-internet-admin-$suffix" 103 'Internet' $entry.Ip @('16443','9100','30080','30081','8080','9000','3000','9090','9093','5432','50000') 'Deny'
}
# SSH from tools is also required by the existing restricted credential-renewal job.
Add-RulePlan $clusterNsgId 'medisur-tools-to-cluster' 102 '172.16.0.5/32' '172.16.0.4' @('22','16443','9100','30080','30081') 'Allow'
Add-RulePlan $toolsNsgId 'medisur-public-webhook' 104 'Internet' '172.16.0.5' @('80','443') 'Allow'

$missingRules = @()
foreach ($rule in $rulePlan) {
    $nsg = $nsgs | Where-Object { $_.id -eq $rule.NsgId }
    if (-not $nsg -and $rule.NsgId -ne $toolsNsgId) { throw 'Cluster NSG is outside the inspected group; manual review required.' }
    $old = @($nsg.securityRules | Where-Object { $_.name -eq $rule.Name -or ($_.direction -eq 'Inbound' -and $_.priority -eq $rule.Priority) })
    if ($old.Count) {
        if ($old.Count -ne 1) { throw "NSG collision for $($rule.Name); no rules overwritten." }
        $source = @($old[0].sourceAddressPrefixes) + @($old[0].sourceAddressPrefix) | Where-Object { $_ }
        $destination = @($old[0].destinationAddressPrefixes) + @($old[0].destinationAddressPrefix) | Where-Object { $_ }
        $ports = @($old[0].destinationPortRanges) + @($old[0].destinationPortRange) | Where-Object { $_ }
        $sourcePorts = @($old[0].sourcePortRanges) + @($old[0].sourcePortRange) | Where-Object { $_ }
        Assert-Equal ($source -join ',') $rule.Source 'NSG source'
        Assert-Equal ($destination -join ',') $rule.Destination 'NSG destination'
        Assert-Equal (($ports | Sort-Object) -join ',') (($rule.Ports | Sort-Object) -join ',') 'NSG ports'
        Assert-Equal ($sourcePorts -join ',') '*' 'NSG source ports'
        Assert-Equal $old[0].access $rule.Access 'NSG access'
        Assert-Equal $old[0].direction 'Inbound' 'NSG direction'
        Assert-Equal $old[0].protocol 'Tcp' 'NSG protocol'
        Assert-Equal $old[0].priority $rule.Priority 'NSG priority'
    } else { $missingRules += $rule }
}
# A preexisting early Allow could defeat a new Deny even without a collision.
# Do not guess about address ranges/service tags: require manual review instead.
foreach ($nsgId in @($clusterNsgId, $toolsNsgId)) {
    $nsg = $nsgs | Where-Object { $_.id -eq $nsgId }
    $plannedPriorities = @($rulePlan | Where-Object { $_.NsgId -eq $nsgId } | ForEach-Object { $_.Priority })
    $unreviewed = @($nsg.securityRules | Where-Object {
        $_.direction -eq 'Inbound' -and $_.access -eq 'Allow' -and $_.priority -le 104 -and
        $plannedPriorities -notcontains $_.priority
    })
    if ($unreviewed.Count) { throw "Early inbound rules on $nsgId require manual review; no network changes applied." }
}
Write-Host 'Missing NSG rules (existing rules are never replaced):'
$missingRules | Format-Table Name, Priority, Source, Destination, Access, @{n='Ports';e={$_.Ports -join ','}} | Out-Host
if ($subnet.networkSecurityGroup) {
    Write-Warning "Subnet NSG $($subnet.networkSecurityGroup.id) also applies. It is NOT changed; inspect effective rules before relying on connectivity."
}
$schedules = @(Invoke-AzJson -Arguments @('resource', 'list', '--resource-group', $resourceGroup,
    '--resource-type', 'Microsoft.DevTestLab/schedules', '--subscription', $subscriptionId))
$missingSchedules = @()
foreach ($vmName in @('LAB-03', $toolsName)) {
    $vmId = "$($group.id)/providers/Microsoft.Compute/virtualMachines/$vmName"
    $scheduleName = "shutdown-computevm-$vmName"
    $existing = @($schedules | Where-Object { $_.name -eq $scheduleName })
    if ($existing.Count) {
        $schedule = Get-ResourceById $existing[0].id
        Assert-Equal $schedule.properties.targetResourceId $vmId 'shutdown target'
        Assert-Equal $schedule.properties.status 'Enabled' 'shutdown status'
        Assert-Equal $schedule.properties.dailyRecurrence.time '0500' 'shutdown time'
        Assert-Equal $schedule.properties.timeZoneId 'UTC' 'shutdown timezone'
    } else { $missingSchedules += $vmName }
}
$budgetUrl = "https://management.azure.com/subscriptions/$subscriptionId/providers/Microsoft.Consumption/budgets"
$budgets = @()
foreach ($scope in @("/subscriptions/$subscriptionId", $group.id)) {
    $nextBudgetPage = "https://management.azure.com$scope/providers/Microsoft.Consumption/budgets?api-version=2023-05-01"
    do {
        $page = Invoke-AzJson -Arguments @('rest', '--method', 'get', '--url', $nextBudgetPage, '--subscription', $subscriptionId)
        $budgets += @($page.value)
        $nextBudgetPage = $page.nextLink
    } while ($nextBudgetPage)
}
$existingBudgets = @($budgets | Where-Object {
    $_.name -eq $BudgetName -or ($_.properties.filter.dimensions.name -eq 'ResourceGroupName' -and
        $_.properties.filter.dimensions.values -contains $resourceGroup) -or $_.id -like "$($group.id)/providers/Microsoft.Consumption/budgets/*"
})
if ($existingBudgets.Count) {
    Write-Host 'Existing budget(s) preserved; compare with budget.json, no PUT/update performed:'
    $existingBudgets | ConvertTo-Json -Depth 20 | Write-Host
}
Write-Host "PLAN: reuse RG/subnet/LAB-03; tools $size, $image, StandardSSD_LRS 64 GB, private 172.16.0.5."
Write-Host "PLAN: DNS $dnsLabel.eastus.cloudapp.azure.com; both shutdowns 0500 UTC; manifest $budgetPath."
Write-Host 'A newly allocated public IP cannot be forced to the documented 20.127.28.221.'
if (-not $Create) { Write-Host 'Read-only plan complete. Pass -Create to apply missing resources and an unset tools DNS label.'; return }

# Finish preflight before any writes, including key derivation and quota checks.
if (-not $tools) {
    if (-not $sku) { throw 'Requested SKU was not returned for eastus.' }
    if (@($sku.restrictions).Count) { throw 'SKU restrictions present; no VM creation attempted.' }
    foreach ($quotaName in @('cores', $sku.family)) {
        $quota = $usage | Where-Object { $_.name.value -eq $quotaName }
        if (-not $quota -or ($quota.limit - $quota.currentValue) -lt 4) { throw "Insufficient/unverified quota: $quotaName" }
    }
    Get-Command ssh-keygen -ErrorAction Stop | Out-Null
    $resolvedKey = (Resolve-Path -LiteralPath $KeyPath).Path
    $publicKeyOutput = & ssh-keygen -y -f $resolvedKey
    if ($LASTEXITCODE -ne 0) { throw 'Public key derivation failed.' }
    $publicKey = ($publicKeyOutput -join "`n").Trim()
    if ($publicKey -notmatch '^ssh-(rsa|ed25519) [A-Za-z0-9+/=]+( .*)?$') { throw 'Unexpected public key format.' }
}
if (-not $existingBudgets.Count -and ([datetime]$budgetManifest.properties.timePeriod.endDate) -le [datetime]::UtcNow) {
    throw 'budget.json has expired; request an updated manifest before creation.'
}
if (-not $toolsNsg) {
    $toolsNsg = Invoke-AzJson -Arguments @('network', 'nsg', 'create', '--resource-group', $resourceGroup,
        '--name', 'medisur-tools-nsg', '--location', $location, '--subscription', $subscriptionId)
}
foreach ($rule in $missingRules) {
    $parts = $rule.NsgId.Split('/')
    $arguments = @('network', 'nsg', 'rule', 'create', '--resource-group', $parts[4], '--nsg-name', $parts[-1],
        '--name', $rule.Name, '--priority', [string]$rule.Priority, '--direction', 'Inbound', '--access', $rule.Access,
        '--protocol', 'Tcp', '--source-address-prefixes', $rule.Source, '--source-port-ranges', '*',
        '--destination-address-prefixes', $rule.Destination, '--destination-port-ranges') + $rule.Ports + @('--subscription', $subscriptionId)
    Invoke-AzJson -Arguments $arguments | Out-Null
}
if (-not $toolsPublicIp) {
    $createdIp = Invoke-AzJson -Arguments @('network', 'public-ip', 'create', '--resource-group', $resourceGroup,
        '--name', 'medisur-tools-ip', '--location', $location, '--sku', 'Standard', '--allocation-method', 'Static',
        '--dns-name', $dnsLabel, '--subscription', $subscriptionId)
    $toolsPublicIp = $createdIp.publicIp
} elseif (-not $toolsPublicIp.dnsSettings.domainNameLabel) {
    Invoke-AzJson -Arguments @('network', 'public-ip', 'update', '--ids', $toolsPublicIp.id,
        '--dns-name', $dnsLabel, '--subscription', $subscriptionId) | Out-Null
}
if (-not $toolsNic) {
    $createdNic = Invoke-AzJson -Arguments @('network', 'nic', 'create', '--resource-group', $resourceGroup,
        '--name', 'medisur-tools-nic', '--location', $location, '--subnet', $subnet.id,
        '--private-ip-address', '172.16.0.5', '--public-ip-address', $toolsPublicIp.id,
        '--network-security-group', $toolsNsgId, '--subscription', $subscriptionId)
    $toolsNic = $createdNic.NewNIC
}
if (-not $tools) {
    Invoke-AzJson -Arguments @('vm', 'create', '--resource-group', $resourceGroup, '--name', $toolsName,
        '--location', $location, '--size', $size, '--image', $image, '--admin-username', 'username',
        '--authentication-type', 'ssh', '--ssh-key-values', $publicKey, '--nics', $toolsNic.id,
        '--os-disk-name', 'medisur-tools-osdisk', '--os-disk-size-gb', '64', '--storage-sku', 'StandardSSD_LRS',
        '--subscription', $subscriptionId) | Out-Null
    $publicKey = $null
}
foreach ($vmName in $missingSchedules) {
    Invoke-AzJson -Arguments @('vm', 'auto-shutdown', '--resource-group', $resourceGroup, '--name', $vmName,
        '--time', '0500', '--subscription', $subscriptionId) | Out-Null
}
if (-not $existingBudgets.Count) {
    Invoke-AzJson -Arguments @('rest', '--method', 'put', '--url', "${budgetUrl}/${BudgetName}?api-version=2023-05-01",
        '--body', "@$budgetPath", '--subscription', $subscriptionId) | Out-Null
}
foreach ($vmName in @('LAB-03', $toolsName)) {
    Invoke-AzJson -Arguments @('vm', 'get-instance-view', '--resource-group', $resourceGroup, '--name', $vmName,
        '--query', 'instanceView.statuses', '--subscription', $subscriptionId) | Format-Table | Out-Host
}
Write-Host 'Azure operations complete. Power state is not SSH/service readiness. No application bootstrap or secrets retrieval performed.'