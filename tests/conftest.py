import pytest

from app import create_app


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    for name in ("NOT_READY", "NOT_READY_FILE", "SQLITE_BUSY_TIMEOUT_MS", "VERSION", "GIT_SHA", "BUILD_DATE"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def application(tmp_path):
    return create_app({"TESTING": True, "DATA_DIR": tmp_path, "SQLITE_BUSY_TIMEOUT_MS": 100})


@pytest.fixture
def client(application):
    return application.test_client()