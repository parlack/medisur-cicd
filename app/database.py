"""SQLite connections are short-lived and never shared between threads or forks."""

from contextlib import contextmanager
from pathlib import Path
import sqlite3
import time


@contextmanager
def connection(database_path, busy_timeout_ms, *, create=False):
    mode = "rwc" if create else "rw"
    database_uri = f"{Path(database_path).resolve().as_uri()}?mode={mode}"
    database = sqlite3.connect(
        database_uri, uri=True, timeout=busy_timeout_ms / 1000, isolation_level=None
    )
    try:
        database.row_factory = sqlite3.Row
        database.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}")
        database.execute("PRAGMA synchronous = FULL")
        yield database
    finally:
        database.close()


def initialize(database_path, busy_timeout_ms):
    # WAL negotiation itself can race when two replicas start on an empty volume.
    deadline = time.monotonic() + busy_timeout_ms / 1000
    while True:
        try:
            with connection(database_path, busy_timeout_ms, create=True) as database:
                journal_mode = database.execute("PRAGMA journal_mode = WAL").fetchone()[0]
                if journal_mode.lower() != "wal":
                    raise sqlite3.OperationalError("WAL mode unavailable")
                database.execute("BEGIN IMMEDIATE")
                try:
                    database.execute(
                        "CREATE TABLE IF NOT EXISTS items ("
                        "id TEXT PRIMARY KEY NOT NULL, "
                        "name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 120))"
                    )
                    database.commit()
                except BaseException:
                    database.rollback()
                    raise
            return
        except sqlite3.OperationalError as error:
            error_code = getattr(error, "sqlite_errorcode", 0) & 0xFF
            if error_code not in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                raise
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.025)


def list_items(database_path, busy_timeout_ms):
    with connection(database_path, busy_timeout_ms) as database:
        return [dict(row) for row in database.execute("SELECT id, name FROM items ORDER BY id")]


def insert_item(database_path, busy_timeout_ms, item):
    with connection(database_path, busy_timeout_ms) as database:
        database.execute("BEGIN IMMEDIATE")
        try:
            database.execute(
                "INSERT INTO items (id, name) VALUES (?, ?)", (item["id"], item["name"])
            )
            database.commit()
        except BaseException:
            database.rollback()
            raise


def check_ready(database_path, busy_timeout_ms):
    with connection(database_path, busy_timeout_ms) as database:
        # Check the application schema, not just whether SQLite can execute SELECT 1.
        database.execute("SELECT id, name FROM items LIMIT 1").fetchone()