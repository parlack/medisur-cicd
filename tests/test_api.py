from pathlib import Path
import runpy
import sqlite3
from uuid import UUID

from prometheus_client.parser import text_string_to_metric_families
import pytest

from app import create_app


def test_health_ready_and_empty_items(client):
    assert client.get("/health").json == {"status": "ok"}
    assert client.get("/ready").json == {"status": "ready"}
    assert client.get("/api/items").json == {"items": []}


def test_version_from_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("VERSION", "1.2.3")
    monkeypatch.setenv("GIT_SHA", "a" * 40)
    monkeypatch.setenv("BUILD_DATE", "2026-11-19T00:00:00Z")
    client = create_app({"TESTING": True}).test_client()
    assert client.get("/version").json == {
        "version": "1.2.3", "git_sha": "a" * 40, "build_date": "2026-11-19T00:00:00Z"
    }


def test_create_item_persists_and_has_uuid(client, application):
    response = client.post("/api/items", json={"name": "  Gauze  "})
    assert response.status_code == 201
    assert response.json["name"] == "Gauze"
    assert UUID(response.json["id"]).version == 4
    second_client = create_app({"TESTING": True, "DATA_DIR": application.config["DATA_DIR"]}).test_client()
    assert second_client.get("/api/items").json == {"items": [response.json]}


@pytest.mark.parametrize("payload", [
    None, [], "item", 3, {}, {"name": None}, {"name": False}, {"name": 12},
    {"name": ""}, {"name": "   "}, {"name": "x" * 121}, {"name": "a\x00b"},
    {"name": "item", "extra": "not allowed"},
])
def test_rejects_invalid_payload(client, payload):
    # Explicit serialization also sends JSON null instead of an empty request.
    import json
    response = client.post("/api/items", data=json.dumps(payload), content_type="application/json")
    assert response.status_code == 400
    assert "error" in response.json
    assert client.get("/api/items").json == {"items": []}


@pytest.mark.parametrize("name", ["x", "x" * 120, "Soluci\u00f3n", "'); DROP TABLE items; --"])
def test_accepts_boundary_and_parameterized_values(client, name):
    response = client.post("/api/items", json={"name": name})
    assert response.status_code == 201
    assert client.get("/api/items").json["items"][0]["name"] == name


def test_invalid_json_content_type_and_size(client):
    assert client.post("/api/items", data='{"name":').status_code == 415
    assert client.post("/api/items", data='{"name":', content_type="application/json").status_code == 400
    assert client.post("/api/items", json={"name": "x" * 17000}).status_code == 413
    assert client.post("/api/items", json={"name": "kit"}, content_type="application/vnd.api+json").status_code == 201


def test_readiness_sentinel_and_env(client, application, monkeypatch):
    marker = Path(application.config["DATA_DIR"]) / "NOT_READY"
    marker.touch()
    assert client.get("/ready").status_code == 503
    assert client.get("/health").status_code == 200
    marker.unlink()
    monkeypatch.setenv("NOT_READY", "true")
    assert client.get("/ready").status_code == 503
    monkeypatch.setenv("NOT_READY", "false")
    assert client.get("/ready").status_code == 200
    application.config["NOT_READY"] = True
    assert client.get("/ready").status_code == 503


def test_custom_readiness_sentinel(tmp_path, monkeypatch):
    marker = tmp_path / "maintenance"
    monkeypatch.setenv("NOT_READY_FILE", str(marker))
    client = create_app({"TESTING": True, "DATA_DIR": tmp_path}).test_client()
    marker.touch()
    assert client.get("/ready").status_code == 503
    marker.unlink()
    assert client.get("/ready").status_code == 200


def test_readiness_checks_real_schema(client, application):
    database = sqlite3.connect(Path(application.config["DATA_DIR"]) / "items.sqlite3")
    try:
        database.execute("DROP TABLE items")
        database.commit()
    finally:
        database.close()
    assert client.get("/ready").status_code == 503
    assert client.get("/api/items").status_code == 503
    assert client.get("/health").status_code == 200


def test_missing_database_is_not_silently_recreated(client, application):
    database_path = Path(application.config["DATA_DIR"]) / "items.sqlite3"
    database_path.unlink()
    assert client.get("/ready").status_code == 503
    assert not database_path.exists()


def test_metrics_have_bounded_route_and_status_labels(client):
    client.get("/health?patient=private")
    client.post("/api/items", json={"name": "item"})
    client.post("/api/items", json={"name": ""})
    client.get("/does-not-exist/123")
    client.get("/does-not-exist/456")
    client.open("/does-not-exist/789", method="ARBITRARY")
    response = client.get("/metrics")
    assert response.status_code == 200
    assert response.content_type.startswith("text/plain")
    text = response.get_data(as_text=True)
    assert "private" not in text
    assert "does-not-exist" not in text
    assert "ARBITRARY" not in text
    samples = [sample for family in text_string_to_metric_families(text) for sample in family.samples]
    requests = [sample for sample in samples if sample.name == "http_requests_total"]
    assert any(sample.labels == {"route": "/api/items", "method": "POST", "status": "201"} for sample in requests)
    assert any(sample.labels["status"] == "400" for sample in requests)
    assert any(sample.labels == {"route": "__unmatched__", "method": "GET", "status": "404"} and sample.value == 2 for sample in requests)
    durations = [sample for sample in samples if sample.name == "http_request_duration_seconds_count"]
    assert {(tuple(sorted(sample.labels.items())), sample.value) for sample in durations} == {
        (tuple(sorted(sample.labels.items())), sample.value) for sample in requests
    }
    assert any(sample.name == "http_request_duration_seconds_bucket" for sample in samples)


def test_http_errors_are_json_with_protocol_headers(client):
    assert client.get("/missing").is_json
    response = client.delete("/api/items")
    assert response.status_code == 405
    assert response.is_json
    assert "GET" in response.headers["Allow"]


def test_invalid_busy_timeout(tmp_path):
    with pytest.raises(ValueError, match="must be positive"):
        create_app({"DATA_DIR": tmp_path, "SQLITE_BUSY_TIMEOUT_MS": 0})


def test_gunicorn_defaults_and_port_override(monkeypatch):
    config_path = Path(__file__).resolve().parents[1] / "app" / "gunicorn.conf.py"
    monkeypatch.delenv("PORT", raising=False)
    config = runpy.run_path(str(config_path))
    assert config["bind"] == "0.0.0.0:8000"
    assert config["workers"] == 1
    assert config["threads"] == 4
    assert not config["preload_app"]
    monkeypatch.setenv("PORT", "9000")
    assert runpy.run_path(str(config_path))["bind"] == "0.0.0.0:9000"