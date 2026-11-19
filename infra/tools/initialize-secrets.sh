#!/usr/bin/env bash
set -euo pipefail
[[ $EUID -eq 0 ]] || { printf 'Run with sudo.\n' >&2; exit 1; }
secret_dir=${1:-/etc/medisur/secrets}
[[ $secret_dir = /* ]] || { printf 'Secret directory must be absolute.\n' >&2; exit 1; }
install -d -m 0700 "$secret_dir"
umask 077
for entry in jenkins_admin_password:1000:1000 grafana_admin_password:472:472 sonar_db_password:1000:1000; do
  IFS=: read -r name owner group <<< "$entry"
  if [[ ! -e "$secret_dir/$name" ]]; then
    # Values are generated only on the VM when explicitly running this script.
    openssl rand -hex 32 > "$secret_dir/$name"
  fi
  [[ -s "$secret_dir/$name" && ! -L "$secret_dir/$name" ]]
  chown "$owner:$group" "$secret_dir/$name"
  chmod 0400 "$secret_dir/$name"
done
# Postgres reads the password as root before dropping privileges; Sonar uses UID
# 1000. Existing volumes keep their database password: do not regenerate it.
printf 'Password files ready (existing files preserved). No passwords printed.\n'
printf 'Still required: monitor-ca.crt and monitor-token owned by 65534:65534, mode 0400.\n'