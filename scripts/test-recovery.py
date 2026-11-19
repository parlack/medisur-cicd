"""Bounded, local-kubectl Medisur pod recovery and reversible rollout demo.

Requires an idle successful Jenkins build 3, the existing laptop HTTP tunnel,
and the explicit LAB-03 admin kubeconfig. Never changes networking or Jenkins.
Evidence contains allowlisted observations, not kubeconfig or command output.
"""
import argparse
import copy
import json
import subprocess
import threading
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


NS = "medisur"
SELECTOR = "app.kubernetes.io/name=medisur"
SHA = "f04534bb54d0a16472ea97009fcebc409e43fb8c"
SERVER = "https://52.151.192.168:16443"
REVISION = "deployment.kubernetes.io/revision"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def run(command, timeout=15):
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError("command_failed_without_logging_output")
    return result.stdout


def http(path):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open("http://localhost:18000" + path, timeout=2) as response:
        if response.status != 200:
            raise RuntimeError("HTTP_not_200")
        return json.load(response)


def template(obj):
    value = copy.deepcopy(obj["spec"]["template"])
    value["metadata"].get("labels", {}).pop("pod-template-hash", None)
    return value


def version_sha(obj):
    return obj["spec"]["template"]["metadata"]["annotations"]["medisur.io/git-sha"]


def revision(obj):
    return int(obj["metadata"]["annotations"][REVISION])


def ready(obj):
    status = obj.get("status", {})
    return (obj["spec"].get("replicas") == 2
            and status.get("observedGeneration", 0) >= obj["metadata"]["generation"]
            and all(status.get(key, 0) == 2 for key in
                    ("replicas", "updatedReplicas", "readyReplicas", "availableReplicas")))


def summary(obj):
    return {"uid": obj["metadata"]["uid"], "revision": revision(obj),
            "generation": obj["metadata"]["generation"], "sha": version_sha(obj),
            "images": [item["image"] for item in obj["spec"]["template"]["spec"]["containers"]],
            "ready_replicas": obj.get("status", {}).get("readyReplicas", 0),
            "available_replicas": obj.get("status", {}).get("availableReplicas", 0)}


def pod_summary(pods):
    return [{"name": pod["metadata"]["name"], "uid": pod["metadata"]["uid"],
             "ready": any(c["type"] == "Ready" and c["status"] == "True"
                          for c in pod.get("status", {}).get("conditions", [])),
             "terminating": bool(pod["metadata"].get("deletionTimestamp"))}
            for pod in pods]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kubeconfig", type=Path, required=True)
    parser.add_argument("--ssh-key", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--execute", action="store_true", help="Explicitly enable bounded mutations")
    args = parser.parse_args()
    if not all(path.is_absolute() for path in (args.kubeconfig, args.ssh_key, args.output)):
        parser.error("All paths must be absolute")
    if args.output.exists():
        parser.error("Evidence already exists; choose a new output to preserve it")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    evidence = {"started_utc": datetime.now(timezone.utc).isoformat(), "namespace": NS,
                "expected_sha": SHA, "mode": "local_kubectl_demo", "mutations": [],
                "status": "blocked", "limitations": [
                    "Single-node SQLite PVC: no node-availability or database rollback proof.",
                    "Pod deletion replaces a UID; it does not imply restartCount increased.",
                    "HTTP samples measure the laptop tunnel path, not all client paths.",
                    "Readiness polling has two-second resolution; timing includes API latency."]}
    base = ["kubectl", "--kubeconfig", str(args.kubeconfig), "--request-timeout=5s", "-n", NS]
    step = "jenkins"
    original = None
    previous = None
    rollback_attempted = False
    stop = threading.Event()
    worker = None

    def kubectl(*arguments):
        return run(base + list(arguments))

    def deployment():
        return json.loads(kubectl("get", "deployment/medisur", "-o", "json"))

    def pods():
        return json.loads(kubectl("get", "pods", "-l", SELECTOR, "-o", "json"))["items"]

    def jenkins():
        value = json.loads(run([
            "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "-i", str(args.ssh_key),
            "username@20.127.28.221", "sudo python3 /tmp/jenkins-control.py status"], timeout=15))
        return {key: value.get(key) for key in ("number", "building", "result")}

    def assert_idle():
        value = jenkins()
        if value != {"number": 3, "building": False, "result": "SUCCESS"}:
            raise RuntimeError("Jenkins_changed_or_not_successful")

    def guard(expected):
        current = deployment()
        if (current["metadata"]["uid"] != original["metadata"]["uid"]
                or template(current) != template(expected)
                or current["spec"].get("replicas") != 2):
            raise RuntimeError("Unexpected_deployment_change_abort")
        return current

    def wait_ready(expected, old_uid=None):
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            current = guard(expected)
            current_pods = pod_summary(pods())
            healthy = (len(current_pods) == 2
                       and all(p["ready"] and not p["terminating"] for p in current_pods))
            if ready(current) and healthy and not any(p["uid"] == old_uid for p in current_pods):
                if http("/ready").get("status") == "ready" and http("/version").get("git_sha") == version_sha(expected):
                    return current, current_pods
            time.sleep(2)
        raise RuntimeError("Ready_wait_exceeded_120_seconds")

    def monitor():
        samples = evidence["http"]["samples"]
        started = time.monotonic()
        while not stop.is_set() and len(samples) < 200 and time.monotonic() - started < 120:
            sample_start = time.monotonic()
            sample = {"elapsed_s": round(sample_start - started, 3)}
            try:
                value = http("/version")
                sample.update(success=value.get("git_sha") == SHA, sha=value.get("git_sha"))
            except Exception as error:
                sample.update(success=False, error=type(error).__name__)
            sample["latency_ms"] = round((time.monotonic() - sample_start) * 1000, 2)
            samples.append(sample)
            stop.wait(max(0, 0.5 - (time.monotonic() - sample_start)))

    try:
        deadline = time.monotonic() + 180
        while True:
            state = jenkins()
            evidence["jenkins"] = state
            if state["number"] != 3:
                raise RuntimeError("Unexpected_Jenkins_build")
            if not state["building"]:
                break
            if time.monotonic() >= deadline:
                raise RuntimeError("Jenkins_still_building_after_180_seconds")
            time.sleep(min(10, max(0, deadline - time.monotonic())))
        assert_idle()
        step = "http_preflight"
        evidence["initial_http"] = {"ready": http("/ready"), "version": http("/version")}
        if (evidence["initial_http"]["ready"].get("status") != "ready"
                or evidence["initial_http"]["version"].get("git_sha") != SHA):
            raise RuntimeError("Unexpected_initial_HTTP_state")
        step = "local_kubernetes_api"
        config = json.loads(kubectl("config", "view", "--minify", "-o", "json"))
        if config["clusters"][0]["cluster"]["server"] != SERVER:
            raise RuntimeError("Not_LAB_03_server")
        evidence["api_server"] = SERVER
        kubectl("get", "--raw=/readyz")
        step = "deployment_preflight"
        original = deployment()
        evidence["before"] = summary(original)
        if not ready(original) or version_sha(original) != SHA or revision(original) != 2:
            raise RuntimeError("Unexpected_initial_deployment")
        sets = json.loads(kubectl("get", "rs", "-l", SELECTOR, "-o", "json"))["items"]
        owned = [rs for rs in sets if any(owner.get("uid") == original["metadata"]["uid"]
                                         for owner in rs["metadata"].get("ownerReferences", []))]
        previous = next(rs for rs in owned if revision(rs) == 1)
        if not version_sha(previous).startswith("6fc0"):
            raise RuntimeError("Unexpected_previous_revision")
        initial_pods = pod_summary(pods())
        evidence["before_pods"] = initial_pods
        if len(initial_pods) != 2 or not all(p["ready"] and not p["terminating"] for p in initial_pods):
            raise RuntimeError("Initial_pods_not_stable")
        if not args.execute:
            evidence["status"] = "preflight_passed_no_mutations"
            return 0
        step = "pod_recovery"
        assert_idle()
        guard(original)
        target = initial_pods[0]
        evidence["http"] = {"interval_s": 0.5, "max_requests": 200, "max_duration_s": 120, "samples": []}
        worker = threading.Thread(target=monitor, daemon=True)
        worker.start()
        time.sleep(0.6)
        started = time.monotonic()
        evidence["mutations"].append({"action": "delete_one_pod", "target": target})
        kubectl("delete", "pod", target["name"], "--wait=false")
        _, after_pods = wait_ready(original, target["uid"])
        evidence["recovery"] = {"seconds_to_two_ready": round(time.monotonic() - started, 3),
                                "after_pods": after_pods}
        worker.join(timeout=122)
        if worker.is_alive():
            raise RuntimeError("HTTP_monitor_timeout")
        if any(sample.get("sha") not in (None, SHA) for sample in evidence["http"]["samples"]):
            raise RuntimeError("Unexpected_HTTP_version_abort")
        step = "rollback"
        assert_idle()
        guard(original)
        rollback_attempted = True
        evidence["mutations"].append({"action": "rollback", "to_revision": revision(previous)})
        started = time.monotonic()
        kubectl("rollout", "undo", "deployment/medisur", "--to-revision", str(revision(previous)))
        rolled, _ = wait_ready(previous)
        evidence["rollback"] = dict(summary(rolled), seconds_to_ready=round(time.monotonic() - started, 3),
                                    http_version=http("/version"))
        evidence["status"] = "tests_completed_pending_restore"
    except Exception as error:
        evidence["failure"] = {"step": step, "type": type(error).__name__,
                               "reason": str(error) if isinstance(error, RuntimeError) else "Operation_failed"}
        evidence["status"] = "failed" if evidence["mutations"] else "blocked"
    finally:
        stop.set()
        if worker is not None:
            worker.join(timeout=3)
        if "http" in evidence:
            samples = evidence["http"]["samples"]
            evidence["http"].update(requests=len(samples), successes=sum(s["success"] for s in samples),
                                    failures=sum(not s["success"] for s in samples))
        if rollback_attempted:
            try:
                current = deployment()
                if template(current) != template(original):
                    guard(previous)
                    assert_idle()
                    evidence["mutations"].append({"action": "restore", "to_revision": revision(original)})
                    kubectl("rollout", "undo", "deployment/medisur", "--to-revision", str(revision(original)))
                restored, final_pods = wait_ready(original)
                evidence["restored"] = dict(summary(restored), pods=final_pods, http_version=http("/version"))
                if evidence["status"] == "tests_completed_pending_restore":
                    evidence["status"] = "passed"
            except Exception as error:
                evidence["status"] = "restore_not_verified_manual_attention_required"
                evidence["restore_error"] = type(error).__name__
        elif original is not None:
            try:
                final, final_pods = wait_ready(original) if evidence["mutations"] else (guard(original), pod_summary(pods()))
                evidence["final"] = dict(summary(final), pods=final_pods)
            except Exception as error:
                evidence["final_verification_error"] = type(error).__name__
        evidence["finished_utc"] = datetime.now(timezone.utc).isoformat()
        with args.output.open("x", encoding="utf-8") as output:
            json.dump(evidence, output, indent=2)
            output.write("\n")
        print(json.dumps({"status": evidence["status"], "output": str(args.output)}))
    return 0 if evidence["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())