param([string]$KeyPath = (Join-Path (Split-Path $PSScriptRoot -Parent | Split-Path -Parent) 'LAB-03_key.pem'))
$ErrorActionPreference = 'Stop'
& ssh -o BatchMode=yes -i $KeyPath username@52.151.192.168 'sudo python3 /home/username/medisur-bootstrap/cluster-credentials.py'
if ($LASTEXITCODE -ne 0) { throw 'Credential issuance failed' }
foreach ($name in @('deployer-kubeconfig', 'monitor-token', 'monitor-ca.crt')) {
    & ssh -o BatchMode=yes -i $KeyPath username@52.151.192.168 "sudo cat /etc/medisur/$name" |
        & ssh -o BatchMode=yes -i $KeyPath username@20.127.28.221 "sudo python3 /tmp/receive-secret.py $name"
    if ($LASTEXITCODE -ne 0) { throw "Credential transfer failed: $name" }
}
& ssh -o BatchMode=yes -i $KeyPath username@20.127.28.221 'sudo chown 65534:65534 /etc/medisur/secrets/monitor-token /etc/medisur/secrets/monitor-ca.crt; sudo chmod 400 /etc/medisur/secrets/monitor-token /etc/medisur/secrets/monitor-ca.crt; sudo python3 /tmp/configure-services.py'
if ($LASTEXITCODE -ne 0) { throw 'Service credential refresh failed' }