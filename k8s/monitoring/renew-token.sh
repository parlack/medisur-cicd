#!/usr/bin/env bash
set -Eeuo pipefail
# Run on a trusted host with a kubeconfig bound to prometheus-token-issuer,
# or an administrator kubeconfig, NOT Jenkins. Never use the scraper token.
# Mount the destination directory (not a single-file bind) into Prometheus.
# Renew hourly; Kubernetes may shorten the requested 24-hour lifetime.
destination=${1:-/etc/prometheus/credentials/kubernetes.token}
[[ $destination == /* ]] || { printf 'An absolute destination is required\n' >&2; exit 2; }
for command in kubectl jq mktemp; do command -v "$command" >/dev/null; done
directory=$(dirname -- "$destination")
[[ -d $directory ]] || { printf 'Create the protected credentials directory first\n' >&2; exit 2; }
umask 077
temporary=$(mktemp "$directory/.kubernetes-token.XXXXXX")
trap 'rm -f -- "$temporary"' EXIT
kubectl --request-timeout=15s -n monitoring create token prometheus-scraper \
  --duration=24h -o json |
  jq -er 'if ((.status.expirationTimestamp | fromdateiso8601) - now) >= 7200
    then .status.token else error("Token expires too soon for hourly renewal") end' > "$temporary"
[[ -s $temporary ]]
chgrp "${PROMETHEUS_GROUP:-prometheus}" "$temporary"
chmod 0640 "$temporary"
mv -f -- "$temporary" "$destination"
printf 'Prometheus token renewed without printing credentials.\n'