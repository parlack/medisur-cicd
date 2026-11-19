FROM docker:28.5.2-cli@sha256:625d9431a9f54c5a2bc90f24f0e1c3d55b1349fd857dd85035f98c2c9acbdd4d AS dockercli
FROM jenkins/jenkins:2.528.3-lts-jdk21@sha256:1e407df1a9bcb122cc4d71d4ce44d2e3b2be31af004c157453a939f13ae691c7

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl unzip jq \
    && rm -rf /var/lib/apt/lists/*
COPY --from=dockercli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=dockercli /usr/local/libexec/docker/cli-plugins/ /usr/local/libexec/docker/cli-plugins/

# Ubuntu tools VM is amd64. Fail instead of silently installing the wrong binary.
ARG KUBECTL_VERSION=v1.34.3
ARG KUBECTL_SHA256=ab60ca5f0fd60c1eb81b52909e67060e3ba0bd27e55a8ac147cbc2172ff14212
RUN test "$(dpkg --print-architecture)" = amd64 \
    && curl -fsSL --retry 3 "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl" -o /tmp/kubectl \
    && printf '%s  /tmp/kubectl\n' "$KUBECTL_SHA256" | sha256sum -c - \
    && install -m 0755 /tmp/kubectl /usr/local/bin/kubectl \
    && rm /tmp/kubectl

ARG SONAR_SCANNER_VERSION=7.3.0.5189
ARG SONAR_SCANNER_SHA256=a251d0793cb6bd889e4fd30299bb5dc4e07433e57133b16fc227aca98f8d2c2d
RUN curl -fsSL --retry 3 "https://binaries.sonarsource.com/Distribution/sonar-scanner-cli/sonar-scanner-cli-${SONAR_SCANNER_VERSION}.zip" -o /tmp/scanner.zip \
    && printf '%s  /tmp/scanner.zip\n' "$SONAR_SCANNER_SHA256" | sha256sum -c - \
    && unzip -q /tmp/scanner.zip -d /opt \
    && ln -s "/opt/sonar-scanner-${SONAR_SCANNER_VERSION}/bin/sonar-scanner" /usr/local/bin/sonar-scanner \
    && rm /tmp/scanner.zip

USER jenkins
COPY --chown=jenkins:jenkins plugins.txt /usr/share/jenkins/ref/plugins.txt
# Plugin Installation Manager resolves transitive dependencies and rejects core
# incompatibilities. Direct versions are pinned; --latest=false uses minimum
# required dependencies, not the moving newest versions of every dependency.
RUN jenkins-plugin-cli --jenkins-version 2.528.3 --latest=false --plugin-file /usr/share/jenkins/ref/plugins.txt
RUN docker --version && docker buildx version && docker compose version \
    && kubectl version --client=true && sonar-scanner --version && jq --version