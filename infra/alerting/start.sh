#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  printf '%s\n' 'Run as root; never pass credentials on the command line.' >&2
  exit 1
fi
directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
python3 "${directory}/prepare-live.py"
docker compose -f "${directory}/compose.yaml" config --quiet
docker compose -f "${directory}/compose.yaml" run --rm --no-deps \
  --entrypoint amtool alertmanager check-config /etc/alertmanager/alertmanager.yaml
# Secret copies are atomic replacements, so recreate to refresh their bind mounts.
docker compose -f "${directory}/compose.yaml" up -d --build --force-recreate --wait --wait-timeout 180
python3 "${directory}/verify-live.py"