pipeline {
    // Manual, isolated trusted job. Placeholders are bound by test-failed-rollout.py.
    // No registry push credentials, webhook, app writes, or main job changes.
    agent { label 'medisur-ci' }
    options {
        disableConcurrentBuilds()
        skipDefaultCheckout(true)
        timestamps()
        timeout(time: 20, unit: 'MINUTES')
    }
    environment {
        SMOKE_BASE_URL = 'http://172.16.0.4:30081'
        EXPECTED_SHA = 'f04534bb54d0a16472ea97009fcebc409e43fb8c'
        // Hexadecimal with letters: an all-digit SHA is parsed as a YAML number.
        CANDIDATE_SHA = 'deadbeefdeadbeefdeadbeefdeadbeefdeadbeef'
        CANDIDATE_IMAGE = 'ghcr.io/sergiomultimedios/medisur-cicd:0.1.0-deadbeefdead'
    }
    stages {
        stage('Trusted unchanged deployment source') {
            steps {
                deleteDir()
                checkout([$class: 'GitSCM', branches: [[name: '*/main']],
                          userRemoteConfigs: [[url: __SCM_URL_JSON__, credentialsId: __SCM_CREDENTIAL_JSON__]]])
                writeFile file: 'evidence/source-checksums.txt', text: __SOURCE_CHECKSUMS_JSON__
                writeFile file: 'evidence/main-config-hash.txt', text: __MAIN_CONFIG_HASH_JSON__
                sh '''#!/usr/bin/env bash
set -Eeuo pipefail
[[ $(git rev-parse HEAD) == "$EXPECTED_SHA" ]]
sha256sum --check evidence/source-checksums.txt
for tool in bash kubectl curl jq; do command -v "$tool" >/dev/null; done
bash -n scripts/deploy.sh scripts/verify.sh
'''
            }
        }
        stage('Real failed rollout and automatic rollback') {
            steps {
                withCredentials([file(credentialsId: 'medisur-kubeconfig', variable: 'KUBECONFIG')]) {
                    sh '''#!/usr/bin/env bash
set -Eeuo pipefail
set +x
kube=(kubectl --namespace=medisur --request-timeout=15s)
monitor_pid=''
stop_monitor() {
  if [[ -n $monitor_pid ]]; then kill "$monitor_pid" 2>/dev/null || true; wait "$monitor_pid" 2>/dev/null || true; fi
}
trap stop_monitor EXIT
"${kube[@]}" get deployment medisur -o json > evidence/before.json
jq -e --arg sha "$EXPECTED_SHA" '
 .metadata.annotations["deployment.kubernetes.io/revision"] == "4" and
 .spec.template.metadata.annotations["medisur.io/git-sha"] == $sha and
 .spec.strategy.rollingUpdate.maxUnavailable == 0 and
 .spec.strategy.rollingUpdate.maxSurge == 1 and .spec.replicas == 2
' evidence/before.json >/dev/null
old_version=$(jq -er '.spec.template.metadata.annotations["medisur.io/version"]' evidence/before.json)
old_image=$(jq -er '.spec.template.spec.containers[] | select(.name == "app") | .image' evidence/before.json)
bash scripts/verify.sh "$EXPECTED_SHA" "$old_version" "$SMOKE_BASE_URL"
pod_snapshot() {
 "${kube[@]}" get pods -l app.kubernetes.io/name=medisur -o json | jq '[.items[] | {
 name:.metadata.name, uid:.metadata.uid, sha:.metadata.annotations["medisur.io/git-sha"],
 terminating:(.metadata.deletionTimestamp != null),
 ready:any(.status.containerStatuses[]?; .name == "app" and .ready == true),
 waiting:[.status.containerStatuses[]? | .state.waiting? | select(. != null)]}]'
}
pod_snapshot > evidence/before-pods.json
: > evidence/samples.jsonl
(
 for ((sample=0; sample<150; sample++)); do
   observed=$(date -u +%Y-%m-%dT%H:%M:%SZ)
   version=$(curl --fail --silent --show-error --connect-timeout 3 --max-time 5 "$SMOKE_BASE_URL/version" 2>/dev/null || printf 'null')
   pods=$(pod_snapshot 2>/dev/null || printf 'null')
   jq -cn --arg at "$observed" --argjson version "$version" --argjson pods "$pods" '{at:$at,version:$version,pods:$pods}' >> evidence/samples.jsonl || true
   sleep 5
 done
) &
monitor_pid=$!
started=$(date -u +%Y-%m-%dT%H:%M:%SZ)
SECONDS=0
set +e
bash scripts/deploy.sh "$CANDIDATE_IMAGE" "$CANDIDATE_SHA" > evidence/deploy.log 2>&1
deploy_status=$?
set -e
deploy_seconds=$SECONDS
cat evidence/deploy.log
set +e
bash scripts/verify.sh "$EXPECTED_SHA" "$old_version" "$SMOKE_BASE_URL"
verify_status=$?
set -e
rescue_attempted=false
if (( verify_status != 0 )); then
  printf 'ALERT: automatic restoration did not verify; invoking real deploy.sh for recovery.\n' >&2
  rescue_attempted=true
  # Emergency recovery uses the same deployment code, never an SSH/manual undo.
  bash scripts/deploy.sh "$old_image" "$EXPECTED_SHA" > evidence/rescue.log 2>&1 || true
  bash scripts/verify.sh "$EXPECTED_SHA" "$old_version" "$SMOKE_BASE_URL" || true
fi
"${kube[@]}" get deployment medisur -o json > evidence/after.json
pod_snapshot > evidence/after-pods.json
curl --fail --silent --show-error --connect-timeout 3 --max-time 10 "$SMOKE_BASE_URL/version" > evidence/after-version.json
stop_monitor
monitor_pid=''
jq -n --slurpfile before evidence/before.json --slurpfile after evidence/after.json \
 --slurpfile before_pods evidence/before-pods.json --slurpfile after_pods evidence/after-pods.json \
 --slurpfile samples evidence/samples.jsonl --slurpfile version evidence/after-version.json \
 --rawfile log evidence/deploy.log --arg started "$started" --arg ended "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
 --argjson exit_code "$deploy_status" --argjson seconds "$deploy_seconds" \
 --argjson verify_exit_code "$verify_status" --argjson rescue "$rescue_attempted" \
 --arg candidate "$CANDIDATE_SHA" --arg expected "$EXPECTED_SHA" '
 {started_utc:$started,finished_utc:$ended,deploy_exit_code:$exit_code,deploy_seconds:$seconds,
 verify_exit_code:$verify_exit_code,rescue_attempted:$rescue,candidate_sha:$candidate,
 start_sha:$before[0].spec.template.metadata.annotations["medisur.io/git-sha"],
 end_sha:$after[0].spec.template.metadata.annotations["medisur.io/git-sha"],
 start_revision:$before[0].metadata.annotations["deployment.kubernetes.io/revision"],
 end_revision:$after[0].metadata.annotations["deployment.kubernetes.io/revision"],
 final_http_version:$version[0],
 original_pods_preserved: (($before_pods[0]|map(.uid)|sort) == ($after_pods[0]|map(.uid)|sort)),
 samples:($samples|length),
 http_samples_failed:([$samples[]|select(.version.git_sha != $expected)]|length),
 samples_below_two_old_ready:([$samples[]|select(([.pods[]?|select(.sha == $expected and .ready)]|length)<2)]|length),
 candidate_pull_failure_observed:any($samples[];.pods != null and any(.pods[];
  .sha == $candidate and any(.waiting[];.reason == "ErrImagePull" or .reason == "ImagePullBackOff"))),
 automatic_rollback_verified: ($exit_code != 0 and $verify_exit_code == 0 and ($rescue|not) and
  $before[0].metadata.uid == $after[0].metadata.uid and
  $before[0].spec.template == $after[0].spec.template and
  $after[0].metadata.annotations["deployment.kubernetes.io/revision"] == "6" and
  $version[0].git_sha == $expected and
  ($log|contains("Deployment failed; restoring revision 4")) and
  ($log|contains("Previous release restored; this build still fails.")) and
  ($log|contains("not found")))
 }' > evidence/result.json
cat evidence/result.json
jq -e '.automatic_rollback_verified and .candidate_pull_failure_observed' evidence/result.json >/dev/null
'''
                }
            }
        }
    }
    post {
        always {
            archiveArtifacts artifacts: 'evidence/*', allowEmptyArchive: true
        }
    }
}