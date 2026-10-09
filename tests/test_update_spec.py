"""scripts/update_spec.py: replaces the bundled spec only with a valid, newer copy, byte for byte."""

import importlib.util
import json

import httpx
import pytest

from conftest import ROOT

spec_ = importlib.util.spec_from_file_location("update_spec", ROOT / "scripts" / "update_spec.py")
assert spec_ and spec_.loader
update_spec = importlib.util.module_from_spec(spec_)
spec_.loader.exec_module(update_spec)

BUNDLED = json.loads((ROOT / "app" / "bundled" / "api.json").read_text("utf-8"))


@pytest.fixture
def bundled(tmp_path, monkeypatch):
    path = tmp_path / "api.json"
    path.write_text(json.dumps(BUNDLED), "utf-8")
    monkeypatch.setattr(update_spec, "BUNDLED", path)
    return path


def serve(monkeypatch, body: bytes, status: int = 200):
    def get(url, **kw):
        return httpx.Response(status, content=body, request=httpx.Request("GET", url))

    monkeypatch.setattr(update_spec.httpx, "get", get)


def test_same_version_changes_nothing(bundled, monkeypatch, capsys):
    before = bundled.read_bytes()
    serve(monkeypatch, json.dumps(BUNDLED).encode())
    assert update_spec.main() == 0
    assert bundled.read_bytes() == before
    assert "нового нет" in capsys.readouterr().out


def test_newer_version_is_written_as_downloaded(bundled, monkeypatch, capsys):
    new = json.loads(json.dumps(BUNDLED))
    new["version"] = "Bot API 99.0"
    new["methods"]["sendBrandNewThing"] = {"name": "sendBrandNewThing", "description": ["new"], "fields": []}
    new["types"]["InputRichBlockHologram"] = {
        "name": "InputRichBlockHologram",
        "fields": [{"name": "type", "types": ["String"], "required": True, "description": "always “hologram”"}],
    }
    new["types"]["InputRichBlock"]["subtypes"].append("InputRichBlockHologram")
    body = json.dumps(new, indent=1).encode()
    serve(monkeypatch, body)
    assert update_spec.main() == update_spec.UPDATED
    assert bundled.read_bytes() == body
    out = capsys.readouterr().out
    assert "Bot API 99.0" in out and "method_added: sendBrandNewThing" in out
    assert "block hologram (InputRichBlockHologram): BLOCK_LABELS" in out and "renderBlock" in out


@pytest.mark.parametrize(
    ("body", "status"),
    [
        (b"not json", 200),
        (b"{}", 200),  # wrong shape
        (b"", 503),
        (json.dumps({**BUNDLED, "version": "Bot API 1.0"}).encode(), 200),  # older
    ],
)
def test_bad_or_older_download_keeps_the_bundled_spec(bundled, monkeypatch, body, status):
    before = bundled.read_bytes()
    serve(monkeypatch, body, status)
    assert update_spec.main() == 1
    assert bundled.read_bytes() == before
