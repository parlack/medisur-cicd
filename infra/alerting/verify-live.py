"""Root-side read-only deployment assertions; outputs no secret material or logs."""
import json
import os
from pathlib import Path
import subprocess


def output(*args):
    return subprocess.check_output(args, stderr=subprocess.STDOUT)


def main():
    secret_dir = Path('/etc/medisur/secrets')
    secrets = []
    for name, uid, mode in [('github-token', 0, 0o600), ('alert-github-token', 10001, 0o400),
                            ('alert-relay-auth', 10001, 0o400), ('alertmanager-relay-auth', 65534, 0o400)]:
        path = secret_dir / name
        stat = path.stat()
        assert stat.st_uid == uid and stat.st_mode & 0o777 == mode
        secrets.append(path.read_bytes().strip())
        print(name, 'owner/mode OK')
    assert secrets[0] == secrets[1] and secrets[2] == secrets[3]
    assert secrets[0] != secrets[2] and all(len(value) >= 20 for value in secrets)
    assert secret_dir.stat().st_mode & 0o777 == 0o700
    for service, uid in [('alert-relay', '10001:10001'), ('alertmanager', '65534:65534')]:
        container = 'medisur-alerting-' + service + '-1'
        config = json.loads(output('docker', 'inspect', container))[0]
        assert config['State']['Health']['Status'] == 'healthy'
        assert config['Config']['User'] == uid
        assert config['HostConfig']['ReadonlyRootfs']
        assert not config['HostConfig']['PortBindings']
        assert config['HostConfig']['Memory'] == 128 * 1024 * 1024
        assert config['HostConfig']['LogConfig']['Config'] == {'max-file': '2', 'max-size': '5m'}
        expected = {'medisur-alerting_delivery', 'medisur-alerting_egress'} if service == 'alert-relay' else {
            'medisur-alerting_delivery', 'medisur-tools_default'}
        assert set(config['NetworkSettings']['Networks']) == expected
        logs = output('docker', 'logs', container)
        assert not any(secret in logs for secret in secrets), 'Secret detected in logs; do not print logs'
        print(service, 'healthy; no published ports; user/network/resource/log checks OK; no secrets in logs')
    mount = '/var/lib/medisur-alerting/state'
    assert os.path.ismount(mount)
    stat = os.statvfs(mount)
    assert 0 < stat.f_blocks * stat.f_frsize <= 512 * 1024 * 1024
    print('Persistence hard limit OK; free MiB:', stat.f_bavail * stat.f_frsize // 1024 // 1024)
    print(output('docker', 'exec', 'medisur-alerting-alertmanager-1',
                 'amtool', 'check-config', '/etc/alertmanager/alertmanager.yaml').decode())


if __name__ == '__main__':
    main()