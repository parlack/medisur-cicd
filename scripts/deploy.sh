#!/usr/bin/env bash
set -Eeuo pipefail

# Usage: bash scripts/deploy.sh ghcr.io/owner/medisur-cicd:0.1.0-SHORT FULL_SHA
# Bootstrap, PVC, registry Secret and monitoring are administrator-owned.
image=${1:?Image is required}
git_sha=${2:?Full Git SHA is required}
[[ $git_sha =~ ^[0-9a-f]{40}$ ]] || { printf 'Invalid full SHA\n' >&2; exit 2; }
version="0.1.0-${git_sha:0:12}"
[[ $image =~ ^ghcr\.io/[a-z0-9][a-z0-9-]*/medisur-cicd:0\.1\.0-[0-9a-f]{12}$ ]] &&
  [[ ${image##*:} == "$version" ]] || { printf 'Image tag must match SHA\n' >&2; exit 2; }
for command in kubectl curl jq sed mktemp; do command -v "$command" >/dev/null; done
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
work=$(mktemp -d)
kube=(kubectl --namespace=medisur --request-timeout=15s)
base_url=${SMOKE_BASE_URL:-http://172.16.0.4:30081}
old_revision=''
old_sha=''
old_version=''
changed=0

diagnostics() {
  "${kube[@]}" get deployment,pods,replicasets,endpoints -l app.kubernetes.io/name=medisur -o wide >&2 || true
  "${kube[@]}" get events --field-selector type=Warning --sort-by=.lastTimestamp >&2 || true
}

finish() {
  result=$?
  trap - EXIT INT TERM
  if (( result != 0 && changed == 1 )); then
    diagnostics
    if [[ -n $old_revision ]]; then
      printf 'Deployment failed; restoring revision %s\n' "$old_revision" >&2
      if "${kube[@]}" rollout undo deployment/medisur --to-revision="$old_revision" &&
         "${kube[@]}" rollout status deployment/medisur --timeout=240s &&
         bash "$root/scripts/verify.sh" "$old_sha" "$old_version" "$base_url"; then
        printf 'Previous release restored; this build still fails. SQLite data was not reverted.\n' >&2
      else
        printf 'ROLLBACK FAILED: operator intervention required.\n' >&2
      fi
    else
      printf 'INITIAL DEPLOYMENT FAILED: no previous revision exists; resources retained for diagnosis.\n' >&2
    fi
  fi
  rm -rf -- "$work"
  exit "$result"
}
trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# A failed API/auth request must not be mistaken for an initial deployment.
"${kube[@]}" get deployment medisur --ignore-not-found -o json > "$work/previous.json"
if [[ -s $work/previous.json ]]; then
  "${kube[@]}" rollout status deployment/medisur --timeout=240s
  "${kube[@]}" get deployment medisur -o json > "$work/previous.json"
  old_revision=$(jq -er '.metadata.annotations["deployment.kubernetes.io/revision"]' "$work/previous.json")
  old_sha=$(jq -er '.spec.template.metadata.annotations["medisur.io/git-sha"]' "$work/previous.json")
  old_version=$(jq -er '.spec.template.metadata.annotations["medisur.io/version"]' "$work/previous.json")
  # Establish a working rollback target before changing anything.
  bash "$root/scripts/verify.sh" "$old_sha" "$old_version" "$base_url"
fi

kubectl kustomize "$root/k8s/app" |
  sed -e "s|ghcr.io/OWNER/medisur-cicd:TAG|$image|g" \
      -e "s|GIT_SHA_PLACEHOLDER|$git_sha|g" \
      -e "s|VERSION_PLACEHOLDER|$version|g" > "$work/release.yaml"
# Server dry-run verifies authorization/admission without storing a release.
"${kube[@]}" apply --dry-run=server -f "$work/release.yaml" >/dev/null
changed=1
"${kube[@]}" apply -f "$work/release.yaml"
"${kube[@]}" rollout status deployment/medisur --timeout=240s
bash "$root/scripts/verify.sh" "$git_sha" "$version" "$base_url"
printf 'Deployed %s\n' "$image"