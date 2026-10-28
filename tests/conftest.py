"""Test fixtures: the studio app wired to the in-process fake Bot API from mock_telegram.py.

Every test gets an empty data folder (database, uploads, spec cache), so tests do not
depend on each other or on their order.
"""

import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest
import uvicorn

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))

from mock_telegram import FAKE  # noqa: E402


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _serve(app) -> int:
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    return port


FAKE_PORT = _serve(FAKE.app)
os.environ.update(
    {
        "DATA_DIR": tempfile.mkdtemp(prefix="rich-studio-test-"),
        "TG_BOT_TOKEN": "123:TEST",
        "TG_API_BASE": f"http://127.0.0.1:{FAKE_PORT}",
        "TG_DEFAULT_CHAT": "@test_channel",
        "TG_PREVIEW_CHAT": "777",
        "ALLOWED_HOSTS": "testserver",
        "TG_UPDATES": "off",  # tests feed updates to handle_update directly
        "STUDIO_BACKGROUND": "off",  # no scheduler racing the tests; they call run_due_posts themselves
        "SPEC_URL": "http://127.0.0.1:9/unreachable",  # keep tests offline: bundled spec only
    }
)
sys.path.insert(0, str(ROOT / "app"))


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient

    import main

    with TestClient(main.app) as c:
        yield c


@pytest.fixture(autouse=True)
def fresh_data(tmp_path, monkeypatch):
    """An empty database and uploads folder for every test."""
    import settings
    import store

    monkeypatch.setattr(settings.get(), "data_dir", tmp_path)
    store.init()
    settings.get().media_dir.mkdir(parents=True, exist_ok=True)
    return tmp_path


PROXY_VARS = (
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "https_proxy",
    "http_proxy",
    "all_proxy",
    "no_proxy",
)


@pytest.fixture(autouse=True)
def no_system_proxy(monkeypatch):
    """Tests run the same with or without a proxy on the machine (a cloud session has one): the studio connects
    directly unless a test sets a proxy itself."""
    for name in PROXY_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def tg():
    FAKE.reset()
    return FAKE


@pytest.fixture
def examples():
    return ROOT / "examples"
