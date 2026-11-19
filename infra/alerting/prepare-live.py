"""Root-only provisioning. Never prints or places secret values in arguments."""
import os
from pathlib import Path
import secrets
import shutil
import subprocess


def run(*args):
    subprocess.run(args, check=True)


def protected_copy(source, target, uid):
    temporary = target.with_suffix('.new')
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(source.read_bytes())
        os.fchown(stream.fileno(), uid, uid)
        os.fsync(stream.fileno())
    temporary.replace(target)


def main():
    if os.geteuid() != 0:
        raise SystemExit('Run as root')
    run('docker', 'network', 'inspect', 'medisur-tools_default', '--format', '{{.Name}}')
    secret_dir = Path('/etc/medisur/secrets')
    source = secret_dir / 'github-token'
    assert source.is_file() and source.stat().st_uid == 0
    assert source.stat().st_mode & 0o777 == 0o600
    protected_copy(source, secret_dir / 'alert-github-token', 10001)
    relay_auth = secret_dir / 'alert-relay-auth'
    if not relay_auth.exists():
        descriptor = os.open(relay_auth, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
        with os.fdopen(descriptor, 'w') as stream:
            stream.write(secrets.token_urlsafe(48))
            os.fchown(stream.fileno(), 10001, 10001)
            os.fsync(stream.fileno())
    os.chown(relay_auth, 10001, 10001)
    os.chmod(relay_auth, 0o400)
    protected_copy(relay_auth, secret_dir / 'alertmanager-relay-auth', 65534)

    root = Path('/var/lib/medisur-alerting')
    root.mkdir(mode=0o755, exist_ok=True)
    image = root / 'state.ext4'
    if not image.exists():
        temporary = root / 'state.ext4.new'
        run('fallocate', '-l', '512M', str(temporary))
        os.chmod(temporary, 0o600)
        run('mkfs.ext4', '-q', '-m', '0', str(temporary))
        temporary.replace(image)
    assert image.stat().st_size == 512 * 1024 * 1024
    mount = root / 'state'
    mount.mkdir(mode=0o755, exist_ok=True)
    unit = 'var-lib-medisur\\x2dalerting-state.mount'
    shutil.copyfile(Path(__file__).parent / 'bounded-state.mount', Path('/etc/systemd/system') / unit)
    run('systemctl', 'daemon-reload')
    run('systemctl', 'enable', '--now', unit)
    run('mountpoint', '-q', str(mount))
    for name, uid in [('relay', 10001), ('alertmanager', 65534)]:
        directory = mount / name
        directory.mkdir(mode=0o700, exist_ok=True)
        os.chown(directory, uid, uid)
        os.chmod(directory, 0o700)
    print('Secret permissions prepared; shared persistent state hard-capped at 512 MiB.')


if __name__ == '__main__':
    main()