#!/usr/bin/env bash
set -euo pipefail
[[ $EUID -eq 0 ]] || { printf 'Run with sudo.\n' >&2; exit 1; }
source /etc/os-release
[[ $ID == ubuntu && $VERSION_ID == 22.04 ]] || { printf 'Requires Ubuntu 22.04.\n' >&2; exit 1; }
[[ $(dpkg --print-architecture) == amd64 ]] || { printf 'Requires amd64.\n' >&2; exit 1; }

# Official signed Docker apt repository, never curl | sh. No cloud/SSH actions.
apt-get update
apt-get install -y ca-certificates curl openssl
install -m 0755 -d /etc/apt/keyrings
curl -fsSL --retry 3 https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
printf 'deb [arch=amd64 signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu jammy stable\n' > /etc/apt/sources.list.d/docker.list
apt-get update
# Use maintained Engine packages. Application container versions are pinned.
# Pin these apt versions separately if an offline/reproducible VM is required.
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
systemctl enable --now docker
map_count=$(sysctl -n vm.max_map_count)
file_max=$(sysctl -n fs.file-max)
(( map_count >= 524288 )) || map_count=524288
(( file_max >= 131072 )) || file_max=131072
printf 'vm.max_map_count=%s\nfs.file-max=%s\n' "$map_count" "$file_max" > /etc/sysctl.d/99-medisur-sonarqube.conf
sysctl --system
install -d -m 0700 /etc/medisur/secrets
printf '\nDocker socket GID for .env: %s\n' "$(stat -c '%g' /var/run/docker.sock)"
printf 'Compose was NOT started. Generate secrets and install monitoring credentials first.\n'