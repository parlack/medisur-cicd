"""Standalone Medisur pilot API. No cloud services are required."""

import os
from pathlib import Path
import sqlite3
import time
from uuid import uuid4

from flask import Flask, Response, g, jsonify, request
from prometheus_client import CollectorRegistry, Counter, Histogram, generate_latest
from prometheus_client.exposition import CONTENT_TYPE_LATEST
from werkzeug.exceptions import HTTPException

from app import database


def create_app(config=None):
    application = Flask(__name__)
    application.config.from_mapping(
        DATA_DIR=os.getenv("DATA_DIR", "/data"),
        SQLITE_BUSY_TIMEOUT_MS=int(os.getenv("SQLITE_BUSY_TIMEOUT_MS", "5000")),
        NOT_READY_FILE=os.getenv("NOT_READY_FILE"),
        NOT_READY=False,
        VERSION=os.getenv("VERSION", "dev"),
        GIT_SHA=os.getenv("GIT_SHA", "unknown"),
        BUILD_DATE=os.getenv("BUILD_DATE", "unknown"),
        MAX_CONTENT_LENGTH=16 * 1024,
    )
    if config:
        application.config.update(config)
    busy_timeout_ms = int(application.config["SQLITE_BUSY_TIMEOUT_MS"])
    if busy_timeout_ms < 1:
        raise ValueError("SQLITE_BUSY_TIMEOUT_MS must be positive")
    data_directory = Path(application.config["DATA_DIR"])
    data_directory.mkdir(parents=True, exist_ok=True)
    database_path = data_directory / "items.sqlite3"
    not_ready_file = Path(application.config["NOT_READY_FILE"] or data_directory / "NOT_READY")
    database.initialize(database_path, busy_timeout_ms)

    registry = CollectorRegistry()
    request_counter = Counter(
        "http_requests_total", "HTTP requests by matched route, method and status.",
        ("route", "method", "status"), registry=registry,
    )
    request_duration = Histogram(
        "http_request_duration_seconds", "Request duration by matched route, method and status.",
        ("route", "method", "status"), registry=registry,
        buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
    )
    application.extensions["prometheus_registry"] = registry

    @application.before_request
    def start_timer():
        g.request_started = time.perf_counter()

    @application.after_request
    def record_metrics(response):
        # Never label with raw paths or query strings: unknown URLs share one label.
        route = request.url_rule.rule if request.url_rule else "__unmatched__"
        method = request.method if request.method in {
            "GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "TRACE", "CONNECT"
        } else "OTHER"
        labels = (route, method, str(response.status_code))
        request_counter.labels(*labels).inc()
        request_duration.labels(*labels).observe(time.perf_counter() - g.request_started)
        return response

    @application.errorhandler(HTTPException)
    def http_error(error):
        # Preserve protocol headers such as Allow on a 405 response.
        response = error.get_response()
        response.data = application.json.dumps({"error": error.name})
        response.content_type = "application/json"
        return response

    @application.errorhandler(sqlite3.Error)
    def database_error(error):
        application.logger.warning("Database operation unavailable: %s", type(error).__name__)
        return jsonify(error="database unavailable"), 503, {"Retry-After": "1"}

    @application.get("/health")
    def health():
        return jsonify(status="ok")

    @application.get("/ready")
    def ready():
        forced_unready = os.getenv("NOT_READY", "").strip().lower() in {"1", "true", "yes", "on"}
        if application.config["NOT_READY"] or forced_unready or not_ready_file.exists():
            return jsonify(status="not ready"), 503
        database.check_ready(database_path, busy_timeout_ms)
        return jsonify(status="ready")

    @application.get("/version")
    def version():
        return jsonify(
            version=application.config["VERSION"],
            git_sha=application.config["GIT_SHA"],
            build_date=application.config["BUILD_DATE"],
        )

    @application.get("/metrics")
    def metrics():
        return Response(generate_latest(registry), content_type=CONTENT_TYPE_LATEST)

    @application.get("/api/items")
    def items():
        return jsonify(items=database.list_items(database_path, busy_timeout_ms))

    @application.post("/api/items")
    def create_item():
        if not request.is_json:
            return jsonify(error="Content-Type must be application/json"), 415
        payload = request.get_json()
        if not isinstance(payload, dict) or set(payload) != {"name"}:
            return jsonify(error="JSON must contain only a name field"), 400
        name = payload["name"]
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 120 or "\x00" in name:
            return jsonify(error="name must be a string of 1 to 120 non-padding characters without NUL"), 400
        item = {"id": str(uuid4()), "name": name.strip()}
        database.insert_item(database_path, busy_timeout_ms, item)
        return jsonify(item), 201

    return application