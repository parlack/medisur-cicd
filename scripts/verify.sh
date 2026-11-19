#!/usr/bin/env bash
set -Eeuo pipefail

# Usage: bash scripts/verify.sh FULL_GIT_SHA VERSION [BASE_URL]
expected_sha=${1:?Expected full Git SHA is required}
expected_version=${2:?Expected image version is required}
base_url=${3:-http://172.16.0.4:30081}
[[ $expected_sha =~ ^[0-9a-f]{40}$ ]] || { printf 'Invalid full SHA\n' >&2; exit 2; }
[[ $expected_version =~ ^0\.1\.0-[0-9a-f]{12}$ ]] || { printf 'Invalid version\n' >&2; exit 2; }
[[ $base_url =~ ^https?://[^[:space:]]+$ ]] || { printf 'Invalid URL\n' >&2; exit 2; }
for command in kubectl curl jq; do command -v "$command" >/dev/null; done
base_url=${base_url%/}
kube=(kubectl --namespace=medisur --request-timeout=15s)

# Check all current replicas, not only whichever replica the Service selects.
deployment=$("${kube[@]}" get deployment medisur -o json)
jq -e --arg sha "$expected_sha" --arg version "$expected_version" '
  .spec.replicas == 2 and
  .status.observedGeneration >= .metadata.generation and
  .status.updatedReplicas == 2 and .status.readyReplicas == 2 and
  .status.availableReplicas == 2 and .status.replicas == 2 and
  .spec.template.metadata.annotations["medisur.io/git-sha"] == $sha and
  .spec.template.metadata.annotations["medisur.io/version"] == $version
' <<< "$deployment" >/dev/null
expected_image=$(jq -er '.spec.template.spec.containers[] | select(.name == "app") | .image' <<< "$deployment")
"${kube[@]}" get pods -l app.kubernetes.io/name=medisur -o json |
  jq -e --arg sha "$expected_sha" --arg image "$expected_image" '
    [.items[] | select(.metadata.deletionTimestamp == null)] |
    length == 2 and all(.[];
      .metadata.annotations["medisur.io/git-sha"] == $sha and
      any(.spec.containers[]; .name == "app" and .image == $image) and
      any(.status.containerStatuses[]?; .name == "app" and .ready == true))
  ' >/dev/null

# Read-only smoke checks; do not create synthetic patient/item data.
for attempt in 1 2 3; do
  curl --fail --silent --show-error --connect-timeout 3 --max-time 10 "$base_url/health" |
    jq -e '.status == "ok"' >/dev/null
  curl --fail --silent --show-error --connect-timeout 3 --max-time 10 "$base_url/ready" |
    jq -e '.status == "ready"' >/dev/null
  curl --fail --silent --show-error --connect-timeout 3 --max-time 10 "$base_url/version" |
    jq -e --arg sha "$expected_sha" --arg version "$expected_version" \
      '.git_sha == $sha and .version == $version' >/dev/null
done
curl --fail --silent --show-error --connect-timeout 3 --max-time 10 "$base_url/api/items" |
  jq -e '.items | type == "array"' >/dev/null
printf 'Verified two ready replicas and HTTP SHA %s (%s)\n' "$expected_sha" "$expected_version"