#!/usr/bin/env bash
set -euo pipefail
[[ $EUID -eq 0 ]] || { printf 'Run with sudo.\n' >&2; exit 1; }
tools_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
cd "$tools_dir"
[[ -f .env ]] || { printf 'Copy .env.example to .env and set DOCKER_GID first.\n' >&2; exit 1; }
# Do not source .env as shell code. Let Compose parse its documented format.
compose=(docker compose --env-file "$tools_dir/.env" -f "$tools_dir/compose.yaml")
"${compose[@]}" config --quiet
[[ $(sysctl -n vm.max_map_count) -ge 524288 ]]
[[ $(stat -c '%g' /var/run/docker.sock) =~ ^[0-9]+$ ]]
# File-backed Compose secrets must exist, be nonempty and be readable by the
# actual nonroot consumers. No dummy credentials or insecure TLS fallback.
"${compose[@]}" run --rm --no-deps --entrypoint /bin/sh prometheus -ec \
  'test -s /run/secrets/monitor-token; test -r /run/secrets/monitor-token; test -s /run/secrets/monitor-ca.crt; promtool check config /etc/prometheus/prometheus.yml'
"${compose[@]}" run --rm --no-deps --entrypoint /bin/promtool \
  -v "$tools_dir/../monitoring/prometheus/rules-tests.yml:/etc/prometheus/rules-tests.yml:ro" \
  prometheus test rules /etc/prometheus/rules-tests.yml
"${compose[@]}" build jenkins
"${compose[@]}" up -d --wait --wait-timeout 900
"${compose[@]}" exec -T jenkins sh -ec \
  'docker version; docker buildx version; kubectl version --client=true; sonar-scanner --version'
"${compose[@]}" ps
printf '\nFinish Sonar initial administrator password rotation over your SSH tunnel.\n'
printf 'Create sonar-token and deployment credentials in Jenkins; none are seeded.\n'