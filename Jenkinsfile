pipeline {
    // Plugins: Pipeline (Declarative), Git, GitHub, Credentials Binding,
    // JUnit, Timestamper, and SonarQube Scanner for Jenkins.
    // Credential IDs: ghcr (username/password), medisur-kubeconfig (file),
    // sonarqube-webhook-secret (secret text). Sonar server config: sonarqube.
    // Configure Sonar's webhook as JENKINS_URL/sonarqube-webhook/ with this
    // shared secret and a coverage >=60% Quality Gate on project medisur-cicd.
    // Trust repository writers: a Docker socket is host-root-equivalent.
    // Do not run untrusted fork PR Jenkinsfiles on this credentialed executor.
    // Dedicated trusted Linux executor with docker CLI/socket, bash, kubectl,
    // curl and jq. Do not use docker agent workspace bind mounts here.
    agent { label 'medisur-ci' }
    options {
        disableConcurrentBuilds()
        timestamps()
        buildDiscarder(logRotator(numToKeepStr: '15', artifactNumToKeepStr: '10'))
        skipDefaultCheckout(true)
        timeout(time: 45, unit: 'MINUTES')
    }
    // Single pipeline: GitHub hook trigger for GITScm polling.
    // Multibranch: also configure GitHub Branch Source webhook discovery.
    triggers { githubPush() }
    parameters {
        string(name: 'GHCR_OWNER', defaultValue: 'sergiomultimedios', description: 'Lowercase GitHub account/organization owning medisur-cicd')
    }
    environment {
        SONAR_SCANNER_IMAGE = 'sonarsource/sonar-scanner-cli:11.1.1.1661_6.2.1@sha256:0b90dedf01ef875d69a5a151f73d72b8288a319b39cdfd2ee32a729027a00785'
        SMOKE_BASE_URL = 'http://172.16.0.4:30081'
    }
    stages {
        stage('Checkout') {
            steps {
                // Prevent stale reports/SCM files from passing a later build.
                deleteDir()
                script {
                    def checkoutResult = checkout scm
                    env.FULL_SHA = sh(script: 'git rev-parse HEAD', returnStdout: true).trim()
                    if (!(env.FULL_SHA ==~ /[0-9a-f]{40}/)) {
                        error('Expected a full 40-character Git commit SHA')
                    }
                    if (!(params.GHCR_OWNER ==~ /[a-z0-9][a-z0-9-]*/)) {
                        error('Set GHCR_OWNER to the lowercase registry owner')
                    }
                    env.APP_VERSION = "0.1.0-${env.FULL_SHA.take(12)}"
                    env.APP_IMAGE = "ghcr.io/${params.GHCR_OWNER}/medisur-cicd:${env.APP_VERSION}"
                    env.BUILD_DATE_UTC = sh(script: 'date -u +%Y-%m-%dT%H:%M:%SZ', returnStdout: true).trim()
                    // The single-branch job must explicitly configure */main.
                    def branchName = env.BRANCH_NAME ?: (checkoutResult.GIT_BRANCH ?: '')
                    env.IS_MAIN = (!env.CHANGE_ID && branchName in ['main', 'origin/main', 'refs/heads/main']) ? 'true' : 'false'
                    echo "SCM branch=${branchName}; publish and deploy=${env.IS_MAIN}"
                    env.TEST_IMAGE = "medisur-ci-test:${env.BUILD_NUMBER}-${env.FULL_SHA.take(12)}"
                }
                sh '''#!/usr/bin/env bash
set -Eeuo pipefail
for tool in bash docker kubectl curl jq; do command -v "$tool" >/dev/null; done
docker info >/dev/null
for script in scripts/deploy.sh scripts/verify.sh k8s/monitoring/renew-token.sh k8s/ci/test-deploy.sh; do bash -n "$script"; done
bash k8s/ci/test-deploy.sh
kubectl kustomize k8s/app >/dev/null
kubectl kustomize k8s/monitoring >/dev/null
'''
            }
        }
        stage('Unit tests and coverage') {
            steps {
                sh '''#!/usr/bin/env bash
set -Eeuo pipefail
container=''
cleanup() { if [[ -n $container ]]; then docker rm -f "$container" >/dev/null 2>&1 || true; fi; }
trap cleanup EXIT
# Dockerfile-specific ignore file includes tests despite the runtime .dockerignore.
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
        stage('SonarQube analysis') {
            steps {
                withSonarQubeEnv('SonarQube') {
                    sh '''#!/usr/bin/env bash
set -Eeuo pipefail
set +x
container=''
cleanup() { if [[ -n $container ]]; then docker rm -f "$container" >/dev/null 2>&1 || true; fi; }
trap cleanup EXIT
export SONAR_TOKEN="$SONAR_AUTH_TOKEN"
container=$(docker create --user 1000:1000 --workdir /usr/src --network medisur-tools_default \
  --env SONAR_TOKEN --env SONAR_HOST_URL "$SONAR_SCANNER_IMAGE" \
  "-Dsonar.projectVersion=$APP_VERSION" "-Dsonar.scm.revision=$FULL_SHA" \
  '-Dsonar.scm.disabled=true')
# Copy only source/report inputs, never Jenkins credential files or .git.
# This works even when Jenkins and the Docker daemon have different filesystems.
docker cp app "$container:/usr/src/app"
docker cp tests "$container:/usr/src/tests"
docker cp sonar-project.properties "$container:/usr/src/sonar-project.properties"
docker start --attach "$container" || true
status=$(docker inspect --format '{{.State.ExitCode}}' "$container")
if [[ $status == 0 ]]; then
  mkdir -p .scannerwork
  docker cp "$container:/tmp/.scannerwork/report-task.txt" .scannerwork/report-task.txt
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
        stage('Build runtime image') {
            steps {
                sh '''#!/usr/bin/env bash
set -Eeuo pipefail
docker build --target runtime --build-arg "VERSION=$APP_VERSION" \
  --build-arg "GIT_SHA=$FULL_SHA" --build-arg "BUILD_DATE=$BUILD_DATE_UTC" \
  -t "$APP_IMAGE" .
'''
            }
        }
        stage('Publish main image') {
            when { expression { env.IS_MAIN == 'true' } }
            steps {
                withCredentials([usernamePassword(credentialsId: 'ghcr', usernameVariable: 'GHCR_USER', passwordVariable: 'GHCR_TOKEN')]) {
                    sh '''#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077
export DOCKER_CONFIG
DOCKER_CONFIG=$(mktemp -d)
trap 'rm -rf -- "$DOCKER_CONFIG"' EXIT
printf '%s' "$GHCR_TOKEN" | docker login ghcr.io --username "$GHCR_USER" --password-stdin
docker push "$APP_IMAGE"
'''
                }
            }
        }
        stage('Deploy main and smoke test') {
            when { expression { env.IS_MAIN == 'true' } }
            steps {
                withCredentials([file(credentialsId: 'medisur-kubeconfig', variable: 'KUBECONFIG')]) {
                    // Rollout and rollback have their own bounds; do not kill
                    // this shell with a shorter stage timeout during rollback.
                    sh 'bash scripts/deploy.sh "$APP_IMAGE" "$FULL_SHA"'
                }
            }
        }
    }
    post {
        always {
            sh '''#!/usr/bin/env bash
if [[ -n ${TEST_IMAGE:-} ]]; then docker image rm "$TEST_IMAGE" >/dev/null 2>&1 || true; fi
if [[ -n ${APP_IMAGE:-} ]]; then docker image rm "$APP_IMAGE" >/dev/null 2>&1 || true; fi
'''
        }
    }
}