"""Install a secret from stdin without logging it; invoke as root on Linux."""
import os
import re
import sys
from pathlib import Path

name = sys.argv[1]
if not re.fullmatch(r'[a-zA-Z0-9_.-]+', name):
    raise SystemExit('Invalid secret filename')
directory = Path('/etc/medisur/secrets')
directory.mkdir(mode=0o700, parents=True, exist_ok=True)
value = sys.stdin.buffer.read(1024 * 1024).strip()
if not value:
    raise SystemExit('Empty secret')
descriptor = os.open(directory / name, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
with os.fdopen(descriptor, 'wb') as output:
    output.write(value)