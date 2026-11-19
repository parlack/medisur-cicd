#!/usr/bin/python3
"""Run on each VM as root; only public keys cross provisioning stdin/stdout."""
import json
import os
from pathlib import Path
import pwd
import subprocess
import sys

BASE = Path(__file__).resolve().parent


def run(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout


def install(source, target, mode):
    # Deployment originates on Windows; Linux shebangs must not contain CRLF.
    Path(target).write_text(Path(source).read_text(), newline='\n')
    os.chown(target, 0, 0)
    os.chmod(target, mode)


def main():
    if os.geteuid() != 0:
        raise RuntimeError('Root required')
    os.umask(0o077)
    action = sys.argv[1]
    if action == 'key':
        directory = Path('/etc/medisur')
        directory.mkdir(mode=0o700, exist_ok=True)
        key = directory / 'cluster-refresh-key'
        if not key.exists():
            run('/usr/bin/ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-C', 'medisur-tools-refresh', '-f', str(key))
        key.chmod(0o600)
        os.chown(key, 0, 0)
        print(run('/usr/bin/ssh-keygen', '-y', '-f', str(key)).strip())
    elif action == 'cluster':
        public = sys.stdin.read().strip().split()
        if len(public) < 2 or public[0] != 'ssh-ed25519':
            raise ValueError('Expected public ed25519 key')
        try:
            pwd.getpwnam('medisurefresh')
        except KeyError:
            run('/usr/sbin/useradd', '--create-home', '--shell', '/usr/bin/bash', 'medisurefresh')
        run('/usr/sbin/usermod', '--lock', '--shell', '/usr/bin/bash', 'medisurefresh')
        account = pwd.getpwnam('medisurefresh')
        if account.pw_uid == 0 or account.pw_dir != '/home/medisurefresh':
            raise ValueError('Unexpected refresh account')
        home = Path(account.pw_dir)
        home.chmod(0o755)
        os.chown(home, 0, 0)
        ssh_dir = home / '.ssh'
        ssh_dir.mkdir(mode=0o755, exist_ok=True)
        os.chown(ssh_dir, 0, 0)
        ssh_dir.chmod(0o755)
        authorized = ssh_dir / 'authorized_keys'
        authorized.write_text('from="172.16.0.5",restrict,command="sudo /usr/local/sbin/medisur-export-credentials" ' + ' '.join(public[:2]) + '\n')
        os.chown(authorized, 0, 0)
        authorized.chmod(0o644)
        install(BASE / 'export-cluster-credentials.py', '/usr/local/sbin/medisur-export-credentials', 0o755)
        rule = Path('/etc/sudoers.d/medisur-refresh')
        rule.write_text('Defaults:medisurefresh env_reset\nmedisurefresh ALL=(root) NOPASSWD: /usr/local/sbin/medisur-export-credentials ""\n')
        rule.chmod(0o440)
        run('/usr/sbin/visudo', '-cf', str(rule))
        print('Restricted cluster refresh account installed')
    elif action == 'tools':
        trusted = {tuple(line.split()[1:3]) for line in sys.stdin.read().splitlines()
                   if line.strip() and not line.startswith('#') and len(line.split()) >= 3}
        scanned = run('/usr/bin/ssh-keyscan', '-T', '10', '172.16.0.4')
        matching = [line for line in scanned.splitlines() if tuple(line.split()[1:3]) in trusted]
        if not matching:
            raise ValueError('Private cluster host key does not match operator known_hosts')
        known = Path('/etc/medisur/cluster-refresh-known-hosts')
        known.write_text('\n'.join(matching) + '\n')
        known.chmod(0o600)
        install(BASE / 'renew-cluster-credentials.py', '/usr/local/sbin/medisur-renew-credentials', 0o755)
        for name in ('medisur-credentials.service', 'medisur-credentials.timer'):
            install(BASE / name, '/etc/systemd/system/' + name, 0o644)
        run('/usr/bin/systemd-analyze', 'verify', '/etc/systemd/system/medisur-credentials.service', '/etc/systemd/system/medisur-credentials.timer')
        run('/usr/bin/systemctl', 'daemon-reload')
        run('/usr/bin/systemctl', 'enable', '--now', 'medisur-credentials.timer')
        run('/usr/bin/systemctl', 'start', 'medisur-credentials.service')
        print('Pinned private host key matched; renewal service and timer installed')
    else:
        raise ValueError('Unknown action')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('Credential provisioning failed (' + type(error).__name__ + ')', file=sys.stderr)
        sys.exit(1)