"""Offline manifest invariants and Bash syntax validation; requires PyYAML."""

from pathlib import Path
import re
import shutil
import subprocess

import yaml


ROOT = Path(__file__).resolve().parents[2]


def documents(relative):
    return list(yaml.safe_load_all((ROOT / relative).read_text(encoding="utf-8")))


all_documents = []
for manifest in (ROOT / "k8s").rglob("*.yaml"):
    loaded = documents(manifest.relative_to(ROOT))
    assert all(isinstance(document, dict) for document in loaded), manifest
    all_documents.extend(loaded)
print(f"PASS YAML parsing: {len(all_documents)} documents")

deployment = documents("k8s/app/deployment.yaml")[0]
spec = deployment["spec"]
assert spec["replicas"] == 2
assert spec["strategy"]["rollingUpdate"] == {"maxUnavailable": 0, "maxSurge": 1}
pod = spec["template"]["spec"]
assert pod["automountServiceAccountToken"] is False
assert pod["securityContext"]["runAsUser"] == 10001
assert pod["securityContext"]["fsGroup"] == 10001
app = pod["containers"][0]
assert app["image"] == "ghcr.io/OWNER/medisur-cicd:TAG"
assert app["securityContext"]["readOnlyRootFilesystem"] is True
assert app["securityContext"]["allowPrivilegeEscalation"] is False
assert app["securityContext"]["capabilities"]["drop"] == ["ALL"]
assert {"requests", "limits"} <= app["resources"].keys()
assert {"startupProbe", "readinessProbe", "livenessProbe"} <= app.keys()
volumes = {volume["name"]: volume for volume in pod["volumes"]}
assert volumes["data"]["persistentVolumeClaim"]["claimName"] == "medisur-data"
assert "emptyDir" in volumes["tmp"]
config = documents("k8s/app/configmap.yaml")[0]
assert config["immutable"] is True
assert not {"VERSION", "GIT_SHA", "BUILD_DATE", "APP_VERSION"} & config["data"].keys()
assert config["data"]["DATA_DIR"] == "/data"
pvc = documents("k8s/bootstrap/namespace.yaml")[2]
assert pvc["spec"]["accessModes"] == ["ReadWriteOnce"]
assert pvc["spec"]["storageClassName"] == "microk8s-hostpath"
print("PASS application security, storage, configuration and rollout invariants")

role = documents("k8s/bootstrap/deployer-rbac.yaml")[1]
assert role["kind"] == "Role" and role["metadata"]["namespace"] == "medisur"
for rule in role["rules"]:
    assert not {"*", "secrets", "roles", "rolebindings", "serviceaccounts/token"} & set(rule["resources"])
    assert "*" not in rule["verbs"]
ksm = documents("k8s/monitoring/kube-state-metrics.yaml")
ksm_role = ksm[1]
assert all(set(rule["verbs"]) == {"list", "watch"} for rule in ksm_role["rules"])
container = ksm[3]["spec"]["template"]["spec"]["containers"][0]
collectors = set(container["args"][0].split("=", 1)[1].split(","))
assert collectors == {resource for rule in ksm_role["rules"] for resource in rule["resources"]}
assert ksm[4]["spec"]["ports"][0]["nodePort"] == 30080
assert documents("k8s/app/services.yaml")[1]["spec"]["ports"][0]["nodePort"] == 30081
template = documents("k8s/templates/ghcr-pull-secret.yaml.template")[0]
assert not {"data", "stringData"} & template.keys()
print("PASS namespace RBAC, monitoring collector permissions and credential-free template")

bash = shutil.which("bash")
windows_bash = Path("C:/Program Files/Git/bin/bash.exe")
if windows_bash.is_file():
    bash = str(windows_bash)
assert bash, "Bash required"
shell_paths = [ROOT / "scripts/deploy.sh", ROOT / "scripts/verify.sh"]
shell_paths.extend((ROOT / "k8s").rglob("*.sh"))
for shell_path in shell_paths:
    subprocess.run([bash, "-n", str(shell_path)], check=True)
jenkins = (ROOT / "Jenkinsfile").read_text(encoding="utf-8")
shell_blocks = re.findall(r"sh '''(.*?)'''", jenkins, re.DOTALL)
assert len(shell_blocks) == 6
for block in shell_blocks:
    subprocess.run([bash, "-n"], input=block, text=True, check=True)
assert "waitForQualityGate abortPipeline: true" in jenkins
assert "disableConcurrentBuilds()" in jenkins
assert ":latest" not in jenkins
assert "docker cp" in jenkins
print(f"PASS Bash syntax: {len(shell_paths)} scripts and {len(shell_blocks)} Jenkins shell blocks")
print("NOTE: no live API, registry, Jenkins compiler, Docker build or jq execution was validated")