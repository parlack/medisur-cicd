"""Offline invariants: python infra/tools/test_configuration.py (PyYAML needed)."""
import json
from pathlib import Path
import unittest

import yaml


TOOLS = Path(__file__).resolve().parent
MONITORING = TOOLS.parent / "monitoring"


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.compose = yaml.safe_load((TOOLS / "compose.yaml").read_text())
        self.prometheus = yaml.safe_load(
            (MONITORING / "prometheus/prometheus.yml").read_text()
        )

    def test_all_yaml_and_json_parse(self):
        files = []
        for root in (TOOLS, MONITORING):
            for path in root.rglob("*"):
                if path.suffix in (".yaml", ".yml"):
                    list(yaml.safe_load_all(path.read_text()))
                    files.append(path)
                elif path.suffix == ".json":
                    json.loads(path.read_text())
                    files.append(path)
        self.assertGreaterEqual(len(files), 9)

    def test_admin_ports_loopback_only(self):
        services = self.compose["services"]
        expected = {"jenkins": 8080, "sonarqube": 9000, "grafana": 3000, "prometheus": 9090}
        for name, port in expected.items():
            self.assertEqual(services[name]["ports"], [f"127.0.0.1:{port}:{port}"])
        self.assertNotIn("ports", services["postgres"])
        self.assertEqual(services["node-exporter"]["network_mode"], "host")
        self.assertIn(
            "--web.listen-address=${TOOLS_PRIVATE_IP:-172.16.0.5}:9100",
            services["node-exporter"]["command"],
        )

    def test_images_are_pinned(self):
        for name, service in self.compose["services"].items():
            self.assertNotIn(":latest", service["image"])
            if name != "jenkins":
                self.assertRegex(service["image"], r":[^:@]+@sha256:[a-f0-9]{64}$")

    def test_authentication_and_file_secrets(self):
        casc = yaml.safe_load((TOOLS / "jenkins/jenkins.yaml").read_text())
        self.assertEqual(casc["jenkins"]["labelString"], "medisur-ci")
        self.assertEqual(casc["unclassified"]["sonarGlobalConfiguration"]["installations"][0]["credentialsId"], "sonar-token")
        local = casc["jenkins"]["securityRealm"]["local"]
        self.assertFalse(local["allowsSignup"])
        self.assertEqual(local["users"][0]["password"], "${jenkins_admin_password}")
        matrix = casc["jenkins"]["authorizationStrategy"]["globalMatrix"]["entries"]
        self.assertEqual(len(matrix), 1)
        self.assertEqual(matrix[0]["user"]["permissions"], ["Overall/Administer"])
        grafana = self.compose["services"]["grafana"]["environment"]
        self.assertEqual(grafana["GF_AUTH_ANONYMOUS_ENABLED"], "false")
        self.assertEqual(grafana["GF_USERS_ALLOW_SIGN_UP"], "false")
        for secret in self.compose["secrets"].values():
            self.assertIn("file", secret)

    def test_retention_and_no_outbound_alerts(self):
        command = self.compose["services"]["prometheus"]["command"]
        self.assertIn("--storage.tsdb.retention.time=15d", command)
        self.assertIn("--storage.tsdb.retention.size=4GB", command)
        self.assertNotIn("alerting", self.prometheus)
        self.assertNotIn("remote_write", self.prometheus)

    def test_per_pod_discovery_and_cadvisor(self):
        jobs = {job["job_name"]: job for job in self.prometheus["scrape_configs"]}
        app = jobs["medisur"]
        self.assertNotIn("static_configs", app)
        self.assertEqual(app["kubernetes_sd_configs"][0]["role"], "endpoints")
        self.assertFalse(app["tls_config"]["insecure_skip_verify"])
        targets = {rule.get("target_label") for rule in app["relabel_configs"]}
        self.assertTrue({"pod", "namespace", "version", "instance"} <= targets)
        self.assertIn("kubernetes-cadvisor", jobs)
        self.assertEqual(jobs["kubernetes-cadvisor"]["authorization"]["credentials_file"], "/run/secrets/monitor-token")

    def test_dashboards_have_valid_datasources_and_nonoverlapping_panels(self):
        dashboards = list((MONITORING / "grafana/dashboards").glob("*.json"))
        self.assertEqual(len(dashboards), 2)
        for path in dashboards:
            document = json.loads(path.read_text())
            occupied = set()
            ids = set()
            for panel in document["panels"]:
                self.assertNotIn(panel["id"], ids)
                ids.add(panel["id"])
                self.assertEqual(panel["datasource"]["uid"], "medisur-prometheus")
                grid = panel["gridPos"]
                self.assertLessEqual(grid["x"] + grid["w"], 24)
                cells = {(column, row) for column in range(grid["x"], grid["x"] + grid["w"])
                         for row in range(grid["y"], grid["y"] + grid["h"])}
                self.assertFalse(occupied & cells)
                occupied |= cells

    def test_bash_files_have_lf_line_endings(self):
        for path in TOOLS.glob("*.sh"):
            content = path.read_bytes()
            self.assertTrue(content.startswith(b"#!/usr/bin/env bash\n"))
            self.assertNotIn(b"\r\n", content)


if __name__ == "__main__":
    unittest.main(verbosity=2)