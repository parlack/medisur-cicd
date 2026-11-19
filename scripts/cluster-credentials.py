"""Issue bounded Kubernetes credentials locally on the trusted cluster node."""
import base64
import json
import os
import subprocess
import sys
from pathlib import Path

directory = Path('/etc/medisur')
directory.mkdir(mode=0o700, parents=True, exist_ok=True)


def run(*arguments, input=None):
    return subprocess.check_output(['microk8s', 'kubectl', *arguments], input=input, text=True).strip()


def save(name, value):
    descriptor = os.open(directory / name, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, 'w') as output:
        output.write(value)


if len(sys.argv) > 1 and sys.argv[1] == 'registry':
    token = sys.stdin.read().strip()
    if not token:
        raise SystemExit('Missing registry credential')
    docker_auth = {'auths': {'ghcr.io': {'auth': base64.b64encode(('SergioMultimedios:' + token).encode()).decode()}}}
    manifest = {'apiVersion': 'v1', 'kind': 'Secret', 'metadata': {'name': 'ghcr-pull', 'namespace': 'medisur'}, 'type': 'kubernetes.io/dockerconfigjson', 'data': {'.dockerconfigjson': base64.b64encode(json.dumps(docker_auth).encode()).decode()}}
    subprocess.run(['microk8s', 'kubectl', 'apply', '-f', '-'], input=json.dumps(manifest), text=True, check=True, stdout=subprocess.DEVNULL)
    print('Registry pull secret configured')
else:
    ca = Path('/var/snap/microk8s/current/certs/ca.crt').read_bytes()
    token = run('-n', 'medisur', 'create', 'token', 'jenkins-deployer', '--duration=24h')
    config = {'apiVersion': 'v1', 'kind': 'Config', 'clusters': [{'name': 'medisur', 'cluster': {'server': 'https://172.16.0.4:16443', 'certificate-authority-data': base64.b64encode(ca).decode()}}], 'users': [{'name': 'jenkins-deployer', 'user': {'token': token}}], 'contexts': [{'name': 'medisur', 'context': {'cluster': 'medisur', 'user': 'jenkins-deployer', 'namespace': 'medisur'}}], 'current-context': 'medisur'}
    save('deployer-kubeconfig', json.dumps(config))
    save('monitor-token', run('-n', 'medisur', 'create', 'token', 'prometheus-tools', '--duration=24h'))
    save('monitor-ca.crt', ca.decode())
    print('24-hour deployment and monitoring credentials renewed')