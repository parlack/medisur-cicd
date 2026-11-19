#!/usr/bin/env bash
set -Eeuo pipefail
# Offline control-flow tests. All Kubernetes and HTTP operations are mocked.
# These do not establish cluster compatibility or validate jq expressions.
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
suite=$(mktemp -d)
trap 'rm -rf -- "$suite"' EXIT
export TEST_SHA=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
export OLD_SHA=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
export TEST_IMAGE=ghcr.io/example/medisur-cicd:0.1.0-aaaaaaaaaaaa

kubectl() {
  printf '%s\n' "$*" >> "$CASE_DIR/commands"
  case "$*" in
    *'get deployment medisur --ignore-not-found'*)
      [[ $CASE != api-failure ]] || return 1
      if [[ $CASE != initial-* ]]; then printf '{"previous":true}\n'; fi ;;
    *'get deployment medisur -o json'*) printf '{"previous":true}\n' ;;
    'kustomize '*) printf 'image: ghcr.io/OWNER/medisur-cicd:TAG\nsha: GIT_SHA_PLACEHOLDER\nversion: VERSION_PLACEHOLDER\n' ;;
    *'apply --dry-run=server'*) [[ $CASE != dry-run-failure ]] ;;
    *'apply -f'*) : > "$CASE_DIR/applied" ;;
    *'rollout undo'*)
      : > "$CASE_DIR/rollback"
      [[ $CASE != rollback-failure ]] ;;
    *'rollout status'*)
      if [[ -f $CASE_DIR/applied && ! -f $CASE_DIR/rollback && $CASE == *rollout-failure ]]; then return 1; fi ;;
  esac
}
jq() {
  case "$*" in
    *'deployment.kubernetes.io/revision'*) printf '7\n' ;;
    *'medisur.io/git-sha'*) printf '%s\n' "$OLD_SHA" ;;
    *'medisur.io/version'*) printf '0.1.0-bbbbbbbbbbbb\n' ;;
    *) return 2 ;;
  esac
}
curl() { printf 'Unexpected network request\n' >&2; return 99; }
bash() {
  if [[ $1 == */scripts/verify.sh ]]; then
    printf 'verify %s %s\n' "$2" "$3" >> "$CASE_DIR/commands"
    if [[ $2 == "$TEST_SHA" && ( $CASE == smoke-failure || $CASE == rollback-failure ) ]]; then return 1; fi
    if [[ $2 == "$OLD_SHA" && $CASE == baseline-failure ]]; then return 1; fi
  else
    command bash "$@"
  fi
}
export -f kubectl jq curl bash

run_case() {
  export CASE=$1 CASE_DIR="$suite/$1"
  mkdir -p "$CASE_DIR"
  : > "$CASE_DIR/commands"
  status=0
  command bash "$root/scripts/deploy.sh" "$TEST_IMAGE" "$TEST_SHA" > "$CASE_DIR/output" 2>&1 || status=$?
  if [[ $2 == success ]]; then
    [[ $status == 0 ]] || { printf 'FAIL %s: unexpected failure\n' "$CASE"; return 1; }
  else
    [[ $status != 0 ]] || { printf 'FAIL %s: unexpected success\n' "$CASE"; return 1; }
  fi
  case "$3" in
    rollback) [[ -f $CASE_DIR/rollback ]] ;;
    no-rollback) [[ ! -f $CASE_DIR/rollback ]] ;;
    no-change) [[ ! -f $CASE_DIR/applied && ! -f $CASE_DIR/rollback ]] ;;
  esac
  if [[ $CASE == initial-rollout-failure ]]; then
    [[ $(< "$CASE_DIR/output") == *'no previous revision exists'* ]]
  fi
  if [[ $CASE == rollback-failure ]]; then
    [[ $(< "$CASE_DIR/output") == *'ROLLBACK FAILED'* ]]
  fi
  printf 'PASS %s\n' "$CASE"
}

run_case initial-success success no-rollback
run_case update-success success no-rollback
run_case update-rollout-failure failure rollback
run_case smoke-failure failure rollback
run_case initial-rollout-failure failure no-rollback
run_case api-failure failure no-change
run_case baseline-failure failure no-change
run_case dry-run-failure failure no-change
run_case rollback-failure failure rollback
status=0
command bash "$root/scripts/deploy.sh" "$TEST_IMAGE" invalid > "$suite/invalid-output" 2>&1 || status=$?
[[ $status == 2 ]]
printf 'PASS invalid-input\n'