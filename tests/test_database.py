from concurrent.futures import ThreadPoolExecutor
import multiprocessing
from pathlib import Path
import sqlite3
import time

import pytest

from app import create_app, database


def _replica_writer(data_directory, start_event):
    if not start_event.wait(timeout=20):
        raise RuntimeError("Replica start timed out")
    application = create_app({"TESTING": True, "DATA_DIR": data_directory})
    with application.test_client() as client:
        for index in range(30):
            response = client.post("/api/items", json={"name": f"item-{index}"})
            if response.status_code != 201:
                raise RuntimeError(f"Write failed: {response.status_code}")


def test_independent_processes_initialize_and_write_shared_database(tmp_path):
    context = multiprocessing.get_context("spawn")
    start_event = context.Event()
    processes = [context.Process(target=_replica_writer, args=(str(tmp_path), start_event)) for _ in range(2)]
    try:
        for process in processes:
            process.start()
        start_event.set()
        for process in processes:
            process.join(timeout=30)
            assert not process.is_alive(), "Replica hung"
            assert process.exitcode == 0
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
            process.close()
    items = database.list_items(tmp_path / "items.sqlite3", 5000)
    assert len(items) == 60
    assert len({item["id"] for item in items}) == 60
    with database.connection(tmp_path / "items.sqlite3", 5000) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        assert connection.execute("PRAGMA synchronous").fetchone()[0] == 2
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_busy_writer_returns_503_without_partial_write(client, application):
    database_path = Path(application.config["DATA_DIR"]) / "items.sqlite3"
    with database.connection(database_path, 100) as lock_holder:
        lock_holder.execute("BEGIN IMMEDIATE")
        response = client.post("/api/items", json={"name": "blocked"})
        assert response.status_code == 503
        assert response.headers["Retry-After"] == "1"
        # WAL readers continue while another process holds the write reservation.
        assert client.get("/ready").status_code == 200
        assert client.get("/api/items").json == {"items": []}
        lock_holder.rollback()
    assert client.post("/api/items", json={"name": "unblocked"}).status_code == 201


def test_writer_waits_for_lock_release(tmp_path):
    application = create_app({"TESTING": True, "DATA_DIR": tmp_path, "SQLITE_BUSY_TIMEOUT_MS": 3000})

    def write_item():
        with application.test_client() as client:
            return client.post("/api/items", json={"name": "waited"}).status_code

    with ThreadPoolExecutor(max_workers=1) as executor:
        with database.connection(tmp_path / "items.sqlite3", 3000) as lock_holder:
            lock_holder.execute("BEGIN IMMEDIATE")
            pending_write = executor.submit(write_item)
            time.sleep(0.15)
            assert not pending_write.done()
            lock_holder.commit()
        assert pending_write.result(timeout=5) == 201


def test_failed_insert_rolls_back_and_releases_lock(tmp_path):
    database_path = tmp_path / "items.sqlite3"
    database.initialize(database_path, 100)
    database.insert_item(database_path, 100, {"id": "existing", "name": "original"})
    with pytest.raises(sqlite3.IntegrityError):
        database.insert_item(database_path, 100, {"id": "existing", "name": "duplicate"})
    database.insert_item(database_path, 100, {"id": "next", "name": "next"})
    assert len(database.list_items(database_path, 100)) == 2


def test_initialize_retries_busy_error(tmp_path, monkeypatch):
    original_connection = database.connection
    attempts = []

    def initially_busy(*args, **kwargs):
        attempts.append(True)
        if len(attempts) == 1:
            error = sqlite3.OperationalError("database is locked")
            error.sqlite_errorcode = sqlite3.SQLITE_BUSY
            raise error
        return original_connection(*args, **kwargs)

    monkeypatch.setattr(database, "connection", initially_busy)
    database.initialize(tmp_path / "items.sqlite3", 100)
    assert len(attempts) == 2


def test_initialize_fails_for_corrupt_database(tmp_path):
    database_path = tmp_path / "items.sqlite3"
    database_path.write_bytes(b"not a database" * 100)
    with pytest.raises(sqlite3.DatabaseError):
        database.initialize(database_path, 100)