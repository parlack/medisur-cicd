# Jenkins security verification - 2026-11-19

The replacement image is built on the tools VM `20.127.28.221`, but is **not deployed**. The running controller remains on `medisur/jenkins:2.528.3-jdk21-tools1`. No live restart, Compose change, live volume modification, registry push, or commit forms part of this security fix.

**Deployment blocker:** the existing JCasC file contains a removed core option. The image is verified, but changing the Compose image alone will fail startup. The integration owner must apply the JCasC correction below during the later maintenance window.

## Verified image and sources

```text
Tag: medisur/jenkins:security-verified-20261119
Local image ID: sha256:43c97b9507d2fe903903f31ad6115fb365fd7aa10c8fa5ac78f6e41f7e4d8925
Local RepoDigest: medisur/jenkins@sha256:43c97b9507d2fe903903f31ad6115fb365fd7aa10c8fa5ac78f6e41f7e4d8925
Platform: linux/amd64
Core: 2.580.2 LTS
Java: 21.0.13+9-LTS
Base: jenkins/jenkins:2.580.2-lts-jdk21@sha256:74401de6a6f92e1afe1b02d1c8e3d0c33f263bfb5275dd8813f9150355f2367b
Base amd64 manifest: sha256:19db581de85e7ce6a1ad89e55a0318d1114e4f103d2dc25493a235ff2b26d044
```

The image is present locally on the tools VM; the tag is not a claim of public registry availability. Docker Hub's explicit version tag and LTS alias returned the same base index digest. The official stable update center identified `2.580.2` as the current LTS during verification on November 19. The October 28 release includes core security fixes.

Official provenance:

- <https://hub.docker.com/v2/repositories/jenkins/jenkins/tags/2.580.2-lts-jdk21>
- <https://hub.docker.com/v2/repositories/jenkins/jenkins/tags/lts-jdk21>
- <https://updates.jenkins.io/stable/update-center.actual.json>
- <https://www.jenkins.io/changelog/2.580.2/>
- <https://www.jenkins.io/security/advisory/2026-10-28/>
- <https://www.jenkins.io/doc/upgrade-guide/2.580/>

## Plugin lock and build enforcement

`infra/tools/jenkins/plugins.txt` pins all 85 plugins: the 12 direct requirements and their complete mandatory dependency closure. The versions come from the official stable update center, not the experimental channel. All selected `requiredCore` values are compatible with `2.580.2`. `github:1.47.0` remains present, and the Dockerfile retains `jq`.

The vulnerable dependency groups reported in the old build now resolve to:

```text
workflow-multibranch:842.v3a_b_59b_57b_e6e
git-client:6.7.1
pipeline-groovy-lib:822.vc95f70dea_db_7
jakarta-mail-api:2.1.5-1
pipeline-build-step:605.v00486e8f4b_3e
workflow-cps:4403.v471713a_cea_19
script-security:1436.v1082791135e4
```

The full lock, rather than this abbreviated list, is authoritative. `jenkins-plugin-cli --jenkins-version 2.580.2 --latest=false` rejects incompatible dependencies. The build extracts the downloaded archives' `Short-Name` and `Plugin-Version` manifest fields and compares the complete sorted inventory against the lock. Missing entries, extra dependencies, changed versions, and duplicate pins fail the comparison. Windows CRLF line endings are normalized for this comparison.

A separate build step downloads the official stable advisory metadata and matches every installed plugin version and the core version against the published warning patterns. Matching advisories, missing warning metadata, invalid JSON, and download errors fail the build. Warning suppression is not enabled.

The verified image contains these evidence files:

```text
/usr/share/jenkins/ref/plugins.resolved.txt
/usr/share/jenkins/ref/jenkins-security-audit.json
/usr/share/jenkins/ref/jenkins-security-metadata.sha256
```

Actual audit output:

```json
{
  "checked_at": "2026-11-19T18:02:43Z",
  "core": "2.580.2",
  "plugin_count": 85,
  "warnings": []
}
```

```text
Advisory metadata SHA256: b0a5fc749c0460997df61b763816f84af0209279ef3062768ba66a21de1240a0
Resolved plugin manifest SHA256: bd366de4fe3ce879353d7c915da10fa432ec8d25b80ef1b05f6d9fe62713654e
```

## Isolated verification

The build context is `/tmp/jenkins-security-20261119` on the tools VM, separate from `/opt/medisur-cicd`. The smoke controller uses `--network none`, no published ports, a disposable tmpfs Jenkins home, a one-CPU limit, and a 2 GiB memory limit. It has no Docker socket, live Jenkins volume, or real credentials.

Verification established:

- Build success, exact archive inventory matching all 85 pins, and zero matching core/plugin advisories.
- All 85 runtime plugins active and enabled, with versions exactly matching the stable lock.
- Corrected JCasC passes `ConfigurationAsCode.checkWith` without findings and applies successfully. The controller retains one executor, label `medisur-ci`, matrix authorization, and an active CSRF crumb issuer. Anonymous API access returns HTTP 403.
- A sandboxed Declarative Pipeline completes with `SUCCESS`, exercising Pipeline CPS, sandbox execution, a shell step, and the bundled tools.
- Docker CLI `28.5.2`, Buildx `0.29.1`, Compose `2.40.3`, kubectl `1.34.3`, SonarScanner `7.3.0.5189`, and jq execute successfully. Git reports `2.47.3`.

The unmodified JCasC fails with `UnknownAttributesException` for `DefaultCrumbIssuer.excludeClientIPFromCrumb`. The corrected configuration was tested in memory on the isolated controller only; the repository and live JCasC file were not edited.

The smoke controller was stopped and removed after verification. The live controller remained healthy with the same start time, `2026-11-19T17:58:31.398111302Z`. Local build inputs and their isolated VM copies have matching SHA256 hashes:

```text
Dockerfile: 424e322933c329514a9273387ddf1f6955f81c28bc444834f307e8ee46db3025
plugins.txt: 61b6e3cca755e9674506c6eae21ce16baffeb9b8508fd12b82675fecf735b573
```

## Deferred integration changes - not executed

Wait for main build `run13f58bbb-d33b-4299-8f4c-1a751581a936` and pipeline integration to finish. Its completion is not established by this image verification. Do not interrupt an active build or restart the live controller now.

Before deployment, the integration owner must replace the existing crumb issuer block in `infra/tools/jenkins/jenkins.yaml` with:

```yaml
jenkins:
  # Keep the other existing Jenkins settings.
  crumbIssuer: standard
```

Only the crumb issuer block changes; the surrounding `jenkins` mapping remains intact. Removing the obsolete IP option does not disable CSRF protection. Do not use a global JCasC unknown-attribute suppression flag to bypass this failure.

The needed later change in `infra/tools/compose.yaml` is:

```yaml
services:
  jenkins:
    image: medisur/jenkins:security-verified-20261119
```

All other service configuration remains unchanged. Both integration files are outside this fix's ownership and were left untouched.

Maintenance sequence for the integration owner:

1. Prevent new builds, wait for running builds and the queue to drain, and record the old image and current configuration. Take a consistent, restorable backup of Jenkins home before the core/plugin upgrade.
2. Apply the JCasC correction and Compose image change. Verify the local image ID against the value above. Use the already-built image, not an incidental rebuild under the old tag.
3. At the approved idle window only, recreate the Jenkins service without rebuilding or changing dependencies:

   ```bash
   sudo docker compose --project-directory /opt/medisur-cicd/infra/tools \
     -f /opt/medisur-cicd/infra/tools/compose.yaml \
     up -d --no-deps --no-build --pull never jenkins
   ```

4. Check core version, successful JCasC startup, authorization, CSRF, every active plugin version, and the security monitor before permitting new builds. Compare the live plugin inventory to the image's `plugins.resolved.txt`; a persistent home may contain manually upgraded, disabled, pinned, or additional plugins that an image change does not automatically reconcile. Any mismatch or additional vulnerable plugin blocks acceptance and requires explicit reconciliation after backup.
5. Run the actual integration pipeline, including GitHub, SonarQube, credentials, Docker builds, and Kubernetes deployment. The isolated test intentionally cannot exercise those live integrations.

Rollback requires restoring the pre-upgrade Jenkins home together with its old image and matching JCasC configuration. Pointing the old core at an already-upgraded plugin directory is not a verified rollback.

## Limits and future checks

Zero matching warnings means zero published Jenkins core/plugin advisories in the metadata fetched at the recorded time. It is not an operating-system CVE scan or a guarantee against undisclosed vulnerabilities. Existing Docker socket privileges remain a separate risk, not a justification for leaving known plugin advisories unfixed.

Core and plugin versions are locked, but Debian packages come from current repositories. The Dockerfile is not a bit-for-bit reproducible operating-system build. The recorded local image identity identifies this verified artifact.

Docker layer caching can reuse an earlier advisory check. A future fresh security assessment requires an uncached build or a fresh scan of the exact artifact; a cached success is not new evidence. Updating any pin requires repeating the complete dependency, advisory, startup, and pipeline checks. Never replace the explicit core tag or plugin versions with `latest`.