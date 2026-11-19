"""Server-local health, JCasC and Docker socket checks without printing secrets."""
import base64
import json
from pathlib import Path
import subprocess
import urllib.request


def get_json(url, token=None):
    headers = {}
    if token:
        auth = base64.b64encode(("medisur-admin:" + token).encode()).decode()
        headers["Authorization"] = "Basic " + auth
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(urllib.request.Request(url, headers=headers), timeout=30) as response:
        return json.load(response), dict(response.headers)


def run(*command, **kwargs):
    return subprocess.run(command, check=True, text=True, **kwargs)


token = Path("/etc/medisur/secrets/jenkins_api_token").read_text().strip()
base = "http://127.0.0.1:8080"
data, headers = get_json(base + "/api/json?tree=nodeName,numExecutors", token)
print("Jenkins:", headers.get("X-Jenkins"), data)
data, _ = get_json(base + "/computer/api/json?tree=computer[displayName,offline,assignedLabels[name]]", token)
assert any(not item["offline"] and {"name": "medisur-ci"} in item["assignedLabels"] for item in data["computer"])
print("Jenkins controller online with medisur-ci label.")
plugins, _ = get_json(base + "/pluginManager/api/json?tree=plugins[shortName,version,active,enabled]", token)
inactive = [item["shortName"] for item in plugins["plugins"] if not item["active"]]
assert not inactive, "Inactive plugins: " + str(inactive)
print("All", len(plugins["plugins"]), "Jenkins plugins active.")
print("JCasC:", next(item["version"] for item in plugins["plugins"] if item["shortName"] == "configuration-as-code"))
print("SonarQube:", get_json("http://127.0.0.1:9000/api/system/status")[0])
print("Grafana:", get_json("http://127.0.0.1:3000/api/health")[0])
run("docker", "exec", "medisur-tools-postgres-1", "postgres", "--version")
run("docker", "exec", "medisur-tools-node-exporter-1", "/bin/node_exporter", "--version")
with urllib.request.urlopen("http://172.16.0.5:9100/metrics", timeout=10) as response:
    assert b"node_exporter_build_info" in response.read()
print("Node exporter metrics HTTP 200.")
run("docker", "exec", "medisur-tools-jenkins-1", "sh", "-ec",
    "docker version; docker buildx version; docker compose version; kubectl version --client=true; sonar-scanner --version")
run("docker", "exec", "-i", "medisur-tools-jenkins-1", "docker", "build", "--no-cache", "-t", "medisur/tools-smoke:local", "-",
    input="FROM scratch\nLABEL purpose=medisur-tools-socket-smoke\n")
run("docker", "ps", "--format", "{{.Names}} | {{.Status}} | {{.Ports}}")
print("Verification completed.")