pipeline {
    // Trusted repository writers only: Docker access is host-root-equivalent.
    // Shared branch job, not a PR sandbox or a per-branch Sonar history.
    agent { label 'medisur-ci' }
    options {
        disableConcurrentBuilds()
        timestamps()
        buildDiscarder(logRotator(numToKeepStr: '15', artifactNumToKeepStr: '10'))
        skipDefaultCheckout(true)
        timeout(time: 30, unit: 'MINUTES')
    }
    triggers { githubPush() }
    environment {
        SONAR_SCANNER_IMAGE = 'sonarsource/sonar-scanner-cli:11.1.1.1661_6.2.1@sha256:0b90dedf01ef875d69a5a151f73d72b8288a319b39cdfd2ee32a729027a00785'
    }
    stages {
        stage('Checkout feature branch') {
            steps {
                deleteDir()
                script {
                    def checkoutResult = checkout scm
                    def branchName = checkoutResult.GIT_BRANCH ?: ''
                    if (env.CHANGE_ID || !(branchName ==~ /origin\/(?!main$).+/)) {
                        error('Feature CI requires a non-main origin branch, never a fork PR')
                    }
                    env.FULL_SHA = sh(script: 'git rev-parse HEAD', returnStdout: true).trim()
                    if (!(env.FULL_SHA ==~ /[0-9a-f]{40}/)) {
                        error('Expected a full 40-character Git commit SHA')
                    }
                    env.TEST_IMAGE = "medisur-feature-test:${env.BUILD_NUMBER}-${env.FULL_SHA.take(12)}"
                    env.APP_VERSION = "feature-${env.BUILD_NUMBER}-${env.FULL_SHA.take(12)}"
                    echo "Feature CI: ${branchName} at ${env.FULL_SHA}; no publish or deploy"
                }
            }
        }
        stage('Unit tests and coverage') {
            steps {
                sh '''#!/usr/bin/env bash
set -Eeuo pipefail
container=''
cleanup() { if [[ -n $container ]]; then docker rm -f "$container" >/dev/null 2>&1 || true; fi; }
trap cleanup EXIT
docker build --target test -f k8s/ci/Dockerfile -t "$TEST_IMAGE" .
container=$(docker create "$TEST_IMAGE")
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
        stage('SonarQube feature analysis') {
            steps {
                withSonarQubeEnv(installationName: 'SonarQube', credentialsId: 'sonar-feature-token') {
                    sh '''#!/usr/bin/env bash
set -Eeuo pipefail
set +x
container=''
cleanup() { if [[ -n $container ]]; then docker rm -f "$container" >/dev/null 2>&1 || true; fi; }
trap cleanup EXIT
export SONAR_TOKEN="$SONAR_AUTH_TOKEN"
container=$(docker create --user 1000:1000 --workdir /usr/src --network medisur-tools_default \
  --env SONAR_TOKEN --env SONAR_HOST_URL "$SONAR_SCANNER_IMAGE" \
  '-Dsonar.projectKey=medisur-feature-ci' '-Dsonar.projectName=MediSur Feature CI' \
  "-Dsonar.projectVersion=$APP_VERSION" "-Dsonar.scm.revision=$FULL_SHA" \
  '-Dsonar.scm.disabled=true' '-Dsonar.working.directory=/tmp/.scannerwork')
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
    }
    post {
        always {
            sh '''#!/usr/bin/env bash
if [[ -n ${TEST_IMAGE:-} ]]; then docker image rm "$TEST_IMAGE" >/dev/null 2>&1 || true; fi
'''
        }
    }
}