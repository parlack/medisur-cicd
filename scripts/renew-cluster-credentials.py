#!/usr/bin/python3
"""Renew local bind-mounted credentials and only the Jenkins file credential."""
import base64
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import ssl
import stat
import subprocess
import sys
import time
import urllib.parse
import urllib.request

SECRETS = Path('/etc/medisur/secrets')
SSH = ['/usr/bin/ssh', '-F', '/dev/null', '-T', '-o', 'BatchMode=yes',
       '-o', 'StrictHostKeyChecking=yes', '-o', 'IdentitiesOnly=yes',
       '-o', 'UserKnownHostsFile=/etc/medisur/cluster-refresh-known-hosts',
       '-o', 'GlobalKnownHostsFile=/dev/null', '-o', 'ConnectTimeout=15',
       '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=3',
       '-i', '/etc/medisur/cluster-refresh-key', 'medisurefresh@172.16.0.4']


def token_expiry(token, account):
    payload = token.split('.')[1]
    claims = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
    if claims['sub'] != 'system:serviceaccount:medisur:' + account:
        raise ValueError('Unexpected token subject')
    remaining = claims['exp'] - time.time()
    if not 12 * 3600 < remaining <= 26 * 3600:
        raise ValueError('Unexpected token lifetime')
    return claims['exp']


def validate(values):
    if set(values) != {'deployer-kubeconfig', 'monitor-token', 'monitor-ca.crt'}:
        raise ValueError('Invalid credential envelope')
    if any(not isinstance(value, str) or not value or len(value) > 131072 for value in values.values()):
        raise ValueError('Invalid credential values')
    config = json.loads(values['deployer-kubeconfig'])
    cluster = config['clusters'][0]['cluster']
    if cluster['server'] != 'https://172.16.0.4:16443' or cluster.get('insecure-skip-tls-verify'):
        raise ValueError('Unexpected cluster endpoint')
    if base64.b64decode(cluster['certificate-authority-data']).decode() != values['monitor-ca.crt']:
        raise ValueError('CA mismatch')
    ssl.create_default_context(cadata=values['monitor-ca.crt'])
    return {
        'deployer_expires_utc': utc(token_expiry(config['users'][0]['user']['token'], 'jenkins-deployer')),
        'monitor_expires_utc': utc(token_expiry(values['monitor-token'], 'prometheus-tools')),
    }


def utc(timestamp=None):
    return datetime.fromtimestamp(time.time() if timestamp is None else timestamp, timezone.utc).isoformat()


def write_in_place(path, value, uid, mode):
    # Compose binds the inode: replacing/renaming the file leaves a stale secret.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, mode)
    with os.fdopen(descriptor, 'r+b') as output:
        metadata = os.fstat(output.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise ValueError('Unsafe secret destination')
        os.fchown(output.fileno(), uid, uid)
        os.fchmod(output.fileno(), mode)
        output.write(value.encode())
        output.truncate()
        output.flush()
        os.fsync(output.fileno())


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def jenkins_script(script):
    token = (SECRETS / 'jenkins_api_token').read_text().strip()
    auth = base64.b64encode(('medisur-admin:' + token).encode()).decode()
    request = urllib.request.Request(
        'http://127.0.0.1:8080/scriptText',
        data=urllib.parse.urlencode({'script': script}).encode(),
        headers={'Authorization': 'Basic ' + auth},
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(request, timeout=90) as response:
        return response.read(1048576).decode().strip()


def update_jenkins(config):
    encoded = base64.b64encode(config.encode()).decode()
    script = '''
import com.cloudbees.plugins.credentials.*
import com.cloudbees.plugins.credentials.domains.Domain
import org.jenkinsci.plugins.plaincredentials.impl.FileCredentialsImpl
def store = SystemCredentialsProvider.getInstance().getStore()
def domain = Domain.global()
def old = store.getCredentials(domain).find { it.id == 'medisur-kubeconfig' }
if (old != null && !(old instanceof FileCredentialsImpl)) throw new IllegalStateException('Credential type mismatch')
def bytes = Base64.decoder.decode('PAYLOAD')
def replacement = new FileCredentialsImpl(old?.scope ?: CredentialsScope.GLOBAL,
    'medisur-kubeconfig', old?.description ?: 'MediSur deployment kubeconfig',
    old?.fileName ?: 'kubeconfig', SecretBytes.fromBytes(bytes))
def changed = old != null ? store.updateCredentials(domain, old, replacement) : store.addCredentials(domain, replacement)
if (!changed) throw new IllegalStateException('Credential update failed')
def saved = store.getCredentials(domain).find { it.id == 'medisur-kubeconfig' }
saved.content.withCloseable { stream ->
    if (!Arrays.equals(stream.bytes, bytes)) throw new IllegalStateException('Credential verification failed')
}
println('MEDISUR_CREDENTIAL_UPDATED')
'''.replace('PAYLOAD', encoded)
    if jenkins_script(script) != 'MEDISUR_CREDENTIAL_UPDATED':
        raise RuntimeError('Jenkins credential update failed')


def main():
    if os.geteuid() != 0:
        raise RuntimeError('Root required')
    os.umask(0o077)
    with open('/run/medisur-credentials/refresh.lock', 'a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('Credential renewal already running')
            return
        result = subprocess.run(SSH, capture_output=True, text=True, check=True, timeout=180)
        values = json.loads(result.stdout)
        status = validate(values)
        for name, value in values.items():
            uid, mode = (0, 0o600) if name == 'deployer-kubeconfig' else (65534, 0o400)
            write_in_place(SECRETS / name, value, uid, mode)
        update_jenkins(values['deployer-kubeconfig'])
        status['last_refresh_utc'] = utc()
        write_in_place(Path('/var/lib/medisur-credentials/status.json'), json.dumps(status) + '\n', 0, 0o600)
        print('Credential renewal succeeded at ' + status['last_refresh_utc'])


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # Never serialize subprocess output, HTTP bodies, tokens or Groovy source.
        print('Credential renewal failed (' + type(error).__name__ + ')', file=sys.stderr)
        sys.exit(1)