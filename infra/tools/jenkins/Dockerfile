FROM docker:28.5.2-cli@sha256:625d9431a9f54c5a2bc90f24f0e1c3d55b1349fd857dd85035f98c2c9acbdd4d AS dockercli
FROM jenkins/jenkins:2.580.2-lts-jdk21@sha256:74401de6a6f92e1afe1b02d1c8e3d0c33f263bfb5275dd8813f9150355f2367b

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
# Pin the full dependency closure, not just direct plugins. Reject missing pins,
# resolver drift, incompatible core requirements, and published security warnings.
RUN jenkins-plugin-cli --jenkins-version 2.580.2 --latest=false \
        --plugin-file /usr/share/jenkins/ref/plugins.txt \
    && for archive in /usr/share/jenkins/ref/plugins/*.jpi; do \
        unzip -p "$archive" META-INF/MANIFEST.MF | tr -d '\r' \
        | awk '/^Short-Name: / {name=$2} /^Plugin-Version: / {version=$2} END {print name ":" version}'; \
       done | LC_ALL=C sort > /tmp/plugins.resolved.txt \
    && awk '{sub(/\r$/, "")} NF && $0 !~ /^#/' /usr/share/jenkins/ref/plugins.txt \
        | LC_ALL=C sort > /tmp/plugins.expected.txt \
    && diff -u /tmp/plugins.expected.txt /tmp/plugins.resolved.txt \
    && cp /tmp/plugins.resolved.txt /usr/share/jenkins/ref/plugins.resolved.txt
RUN curl -fsSL --retry 3 https://updates.jenkins.io/stable/update-center.actual.json \
        -o /tmp/update-center.security.json \
    && jq -e --rawfile lock /usr/share/jenkins/ref/plugins.resolved.txt \
        --arg core 2.580.2 '\
        if (.warnings | type) != "array" then error("Missing advisory metadata") else . end \
        | ($lock | split("\n") | map(select(length > 0) | split(":") \
            | {key: .[0], value: .[1]}) | from_entries) as $plugins \
        | [.warnings[] | . as $warning \
            | (if .type == "core" then $core else $plugins[.name] end) as $version \
            | select($version != null) \
            | select(any(.versions[]; .pattern as $pattern \
                | $version | test("^(?:" + $pattern + ")$"))) \
            | {id, name, message, url, version: $version}] as $warnings \
        | {checked_at: (now | todateiso8601), core: $core, \
            plugin_count: ($plugins | length), warnings: $warnings} \
        | if (.warnings | length) == 0 then . else error(tojson) end' \
        /tmp/update-center.security.json > /usr/share/jenkins/ref/jenkins-security-audit.json \
    && sha256sum /tmp/update-center.security.json \
        > /usr/share/jenkins/ref/jenkins-security-metadata.sha256 \
    && cat /usr/share/jenkins/ref/jenkins-security-audit.json \
    && rm /tmp/update-center.security.json /tmp/plugins.expected.txt /tmp/plugins.resolved.txt
RUN docker --version && docker buildx version && docker compose version \
    && kubectl version --client=true && sonar-scanner --version && jq --version