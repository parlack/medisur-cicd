pipeline {
    // Manual-only negative control from the same trusted main SCM as medisur-main.
    // Provision with scripts/configure-gate-demo.py; never attach a push trigger.
    // No registry or Kubernetes credentials, runtime build, push, or deployment.
    // This is NOT a security sandbox: the lab controller's Docker socket is
    // host-root-equivalent and global Jenkins credentials remain a trust boundary.
    agent { label 'medisur-ci' }
    options {
        disableConcurrentBuilds()
        timestamps()
        buildDiscarder(logRotator(numToKeepStr: '10', artifactNumToKeepStr: '10'))
        skipDefaultCheckout(true)
        timeout(time: 30, unit: 'MINUTES')
    }
    environment {
        SONAR_SCANNER_IMAGE = 'sonarsource/sonar-scanner-cli:11.1.1.1661_6.2.1@sha256:0b90dedf01ef875d69a5a151f73d72b8288a319b39cdfd2ee32a729027a00785'
    }
    stages {
        stage('Checkout') {
            steps {
                deleteDir()
                script {
                    def checkoutResult = checkout scm
                    def branchName = env.BRANCH_NAME ?: (checkoutResult.GIT_BRANCH ?: '')
                    if (env.CHANGE_ID || !(branchName in ['main', 'origin/main', 'refs/heads/main'])) {
                        error('Quality demo requires the trusted main SCM, not a PR or demo branch')
                    }
                    env.FULL_SHA = sh(script: 'git rev-parse HEAD', returnStdout: true).trim()
                    if (!(env.FULL_SHA ==~ /[0-9a-f]{40}/)) {
                        error('Expected a full 40-character Git commit SHA')
                    }
                    // A distinct namespace avoids collisions with the main job's test image.
                    env.TEST_IMAGE = "medisur-gate-demo-test:${env.BUILD_NUMBER}-${env.FULL_SHA.take(12)}"
                    env.APP_VERSION = "gate-demo-${env.BUILD_NUMBER}-${env.FULL_SHA.take(12)}"
                }
                sh '''#!/usr/bin/env bash
set -Eeuo pipefail
for tool in bash docker; do command -v "$tool" >/dev/null; done
docker info >/dev/null
# The fixture belongs in ci/, never in the committed/runtime application.
[[ ! -e app/_quality_demo.py ]]
cp ci/demo-uncovered.py app/_quality_demo.py
'''
            }
        }
        stage('Unit tests and deliberately low coverage') {
            steps {
                sh '''#!/usr/bin/env bash
set -Eeuo pipefail
container=''
cleanup() { if [[ -n $container ]]; then docker rm -f "$container" >/dev/null 2>&1 || true; fi; }
trap cleanup EXIT
docker build --target test -f k8s/ci/Dockerfile -t "$TEST_IMAGE" .
# Override only pytest's coverage floor; real test failures still fail this stage.
# Sonar, not pytest, must demonstrate the blocking coverage gate.
container=$(docker create "$TEST_IMAGE" python -m pytest --cov-fail-under=0)
docker start --attach "$container" || true
status=$(docker inspect --format '{{.State.ExitCode}}' "$container")
mkdir -p tests/reports
docker cp "$container:/workspace/tests/reports/." tests/reports/
[[ -s tests/reports/junit.xml && -s tests/reports/coverage.xml ]]
exit "$status"
'''
            }
            post {
                always {
                    junit testResults: 'tests/reports/junit.xml', allowEmptyResults: false
                    archiveArtifacts artifacts: 'tests/reports/junit.xml,tests/reports/coverage.xml', allowEmptyArchive: true
                }
            }
        }
        stage('SonarQube demo analysis') {
            steps {
                withSonarQubeEnv(installationName: 'SonarQube', credentialsId: 'sonar-demo-token') {
                    sh '''#!/usr/bin/env bash
set -Eeuo pipefail
set +x
container=''
cleanup() { if [[ -n $container ]]; then docker rm -f "$container" >/dev/null 2>&1 || true; fi; }
trap cleanup EXIT
export SONAR_TOKEN="$SONAR_AUTH_TOKEN"
container=$(docker create --user 1000:1000 --workdir /usr/src --network medisur-tools_default \
  --env SONAR_TOKEN --env SONAR_HOST_URL "$SONAR_SCANNER_IMAGE" \
  '-Dsonar.projectKey=medisur-quality-demo' '-Dsonar.projectName=MediSur Quality Gate Demo' \
  "-Dsonar.projectVersion=$APP_VERSION" "-Dsonar.scm.revision=$FULL_SHA" \
  '-Dsonar.scm.disabled=true' '-Dsonar.working.directory=/tmp/.scannerwork')
# Same source/report settings as main, with an explicit separate project key.
# Never copy .git, the full workspace, or Jenkins credential files.
docker cp app "$container:/usr/src/app"
docker cp tests "$container:/usr/src/tests"
docker cp sonar-project.properties "$container:/usr/src/sonar-project.properties"
docker start --attach "$container" || true
status=$(docker inspect --format '{{.State.ExitCode}}' "$container")
if [[ $status == 0 ]]; then
  mkdir -p .scannerwork
  docker cp "$container:/tmp/.scannerwork/report-task.txt" .scannerwork/report-task.txt
  [[ -s .scannerwork/report-task.txt ]]
fi
exit "$status"
'''
                }
            }
        }
        stage('Quality gate') {
            steps {
                timeout(time: 10, unit: 'MINUTES') {
                    waitForQualityGate abortPipeline: true, webhookSecretId: 'sonarqube-webhook-secret'
                }
            }
        }
        stage('Would publish') {
            steps {
                // This sentinel must be SKIPPED because Sonar rejected coverage.
                // Reaching it is a broken demo, not successful proof of blocking.
                error('Unexpected gate pass: Would publish must never run; nothing was published')
            }
        }
    }
    post {
        always {
            sh '''#!/usr/bin/env bash
if [[ -n ${TEST_IMAGE:-} ]]; then docker image rm "$TEST_IMAGE" >/dev/null 2>&1 || true; fi
# Remove only the job-local fixture, leaving archived reports as evidence.
rm -f -- app/_quality_demo.py
'''
        }
    }
}
