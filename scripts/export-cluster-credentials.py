#!/usr/bin/python3
"""Root-only forced SSH command. Stdout is exclusively the credential envelope."""
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys


def main():
    if os.geteuid() != 0 or len(sys.argv) != 1:
        raise RuntimeError("Invalid invocation")
    os.umask(0o077)
    with open('/run/medisur-export-credentials.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        subprocess.run(
            ['/usr/bin/python3', '/home/username/medisur-bootstrap/cluster-credentials.py'],
            check=True, capture_output=True, timeout=120,
            env={'PATH': '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/snap/bin'},
        )
        values = {}
        for name in ('deployer-kubeconfig', 'monitor-token', 'monitor-ca.crt'):
            path = Path('/etc/medisur') / name
            path.chmod(0o600)
            values[name] = path.read_text()
        sys.stdout.write(json.dumps(values))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('Credential export failed', file=sys.stderr)
        sys.exit(1)