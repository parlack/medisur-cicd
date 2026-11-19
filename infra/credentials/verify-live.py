#!/usr/bin/python3
"""Print only non-secret evidence of forced-command confinement and rotation."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import time
import urllib.request

spec = importlib.util.spec_from_file_location('renew', '/usr/local/sbin/medisur-renew-credentials')
# Installed executable intentionally has no .py extension.
if spec is None:
    from importlib.machinery import SourceFileLoader
    spec = importlib.util.spec_from_loader('renew', SourceFileLoader('renew', '/usr/local/sbin/medisur-renew-credentials'))
renew = importlib.util.module_from_spec(spec)
spec.loader.exec_module(renew)


def run(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=300).stdout.strip()


def job_fingerprint():
    return renew.jenkins_script('''
import jenkins.model.Jenkins
import java.security.MessageDigest
def digest = MessageDigest.getInstance('SHA-256')
Jenkins.get().getAllItems(hudson.model.Job.class).sort { it.fullName }.each { job ->
    digest.update(job.fullName.getBytes('UTF-8'))
    digest.update(job.configFile.asString().getBytes('UTF-8'))
}
println(digest.digest().encodeHex().toString())
''')


def main():
    paths = [renew.SECRETS / name for name in ('deployer-kubeconfig', 'monitor-token', 'monitor-ca.crt')]
    inodes = [path.stat().st_ino for path in paths]
    old_monitor = paths[1].read_bytes()
    jobs_before = job_fingerprint()
    if len(jobs_before) != 64:
        raise RuntimeError('Could not verify Jenkins job configuration')
    forced = run(*renew.SSH, 'printf ARBITRARY_COMMAND_EXECUTED')
    envelope = json.loads(forced)
    renew.validate(envelope)
    print('forced_command_arbitrary_command_blocked=true')
    time.sleep(2)
    run('/usr/bin/systemctl', 'start', 'medisur-credentials.service')
    if inodes != [path.stat().st_ino for path in paths]:
        raise RuntimeError('Bind mount inode changed')
    if old_monitor == paths[1].read_bytes():
        raise RuntimeError('Monitor token did not rotate')
    mounted_hash = run('/usr/bin/docker', 'exec', 'medisur-tools-prometheus-1',
                       'sha256sum', '/run/secrets/monitor-token').split()[0]
    if mounted_hash != hashlib.sha256(paths[1].read_bytes()).hexdigest():
        raise RuntimeError('Prometheus bind mount token is stale')
    if job_fingerprint() != jobs_before:
        raise RuntimeError('Jenkins job configuration changed during verification')
    expected = hashlib.sha256(paths[0].read_bytes()).hexdigest()
    verified = renew.jenkins_script('''
import com.cloudbees.plugins.credentials.SystemCredentialsProvider
import com.cloudbees.plugins.credentials.domains.Domain
import java.security.MessageDigest
def credential = SystemCredentialsProvider.getInstance().getStore().getCredentials(Domain.global()).find { it.id == 'medisur-kubeconfig' }
credential.content.withCloseable { stream ->
    println(MessageDigest.getInstance('SHA-256').digest(stream.bytes).encodeHex().toString() == 'EXPECTED')
}
'''.replace('EXPECTED', expected))
    if verified != 'true':
        raise RuntimeError('Jenkins credential mismatch')
    print('jenkins_credential_matches=true jobs_unchanged=true bind_mount_inodes_preserved=true monitor_token_rotated=true')
    print('prometheus_mounted_token_matches=true')
    for path in paths:
        meta = path.stat()
        print(f'{path.name}: uid={meta.st_uid} mode={meta.st_mode & 0o777:o}')
    print(Path('/var/lib/medisur-credentials/status.json').read_text().strip())
    print(run('/usr/bin/systemctl', 'show', 'medisur-credentials.service', '-p', 'ActiveState', '-p', 'SubState', '-p', 'Result', '-p', 'ExecMainStatus'))
    print(run('/usr/bin/systemctl', 'show', 'medisur-credentials.timer', '-p', 'ActiveState', '-p', 'SubState', '-p', 'NextElapseUSecRealtime'))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('Live verification failed (' + type(error).__name__ + ')', file=sys.stderr)
        sys.exit(1)