"""/docs: interactive API docs that run only the studio's own scripts, under its CSP, and work offline.

The page is behind the guard like the API itself; the schema says how to send a key, so «Authorize» and
«Try it out» in Swagger UI work against the studio. The browser side (the page draws, no CSP violations, nothing
leaves the studio) is checked by tests/e2e/38-api-docs.mjs.
"""

from html.parser import HTMLParser

import settings


class _Page(HTMLParser):
    """The page's scripts with their inline bodies, and every address it loads."""

    def __init__(self) -> None:
        super().__init__()
        self.scripts: list[dict] = []
        self.urls: list[str] = []
        self._script: dict | None = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "script":
            self._script = {"src": a.get("src"), "body": ""}
            self.scripts.append(self._script)
        self.urls += [v for k, v in attrs if k in ("src", "href") and v]
        if any(k.startswith("on") for k in a):
            self.scripts.append({"src": None, "body": f"<{tag}> event handler attribute"})

    def handle_endtag(self, tag):
        if tag == "script":
            self._script = None

    def handle_data(self, data):
        if self._script is not None:
            self._script["body"] += data


def _csp(header: str) -> dict[str, list[str]]:
    return {words[0]: words[1:] for part in header.split(";") if (words := part.split())}


def test_docs_page_runs_only_the_studios_own_scripts(client):
    r = client.get("/docs")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    csp = _csp(r.headers.get("content-security-policy", ""))
    assert csp.get("script-src") == ["'self'"], csp
    assert csp.get("connect-src") == ["'self'"], csp  # the contract and «Try it out» go to the studio only
    page = _Page()
    page.feed(r.text)
    assert page.scripts, "no scripts: the page would not draw"
    assert all(s["src"] and not s["body"].strip() for s in page.scripts), page.scripts  # nothing inline
    for url in page.urls:
        assert url.startswith("/") and not url.startswith("//"), url  # nothing from a CDN
        assert client.get(url).status_code == 200, url  # works offline: every file is in the studio


def test_redoc_is_gone(client):
    assert client.get("/redoc").status_code == 404


def test_docs_need_the_key_or_the_editors_session(client, monkeypatch):
    monkeypatch.setattr(settings.get(), "api_key", "secret")
    for path in ("/docs", "/openapi.json"):
        assert client.get(path).status_code == 401, path
        assert client.get(path, headers={"X-API-Key": "secret"}).status_code == 200, path
    assert client.post("/api/session", json={"key": "secret"}).status_code == 200  # logged in to the editor
    try:
        assert client.get("/docs").status_code == 200
        assert client.get("/openapi.json").status_code == 200
    finally:
        client.cookies.clear()


def test_schema_tells_swagger_how_to_send_a_key_the_guard_accepts(client, monkeypatch):
    schema = client.get("/openapi.json").json()
    schemes = schema["components"]["securitySchemes"]
    assert {"type": "apiKey", "in": "header", "name": "X-API-Key"}.items() <= schemes["api_key"].items()
    assert {"type": "http", "scheme": "bearer"}.items() <= schemes["token"].items()
    # any of them, or none at all while the studio has neither a key nor tokens
    assert schema["security"] == [{"token": []}, {"api_key": []}, {}]
    monkeypatch.setattr(settings.get(), "api_key", "secret")
    assert client.get("/api/drafts").status_code == 401
    for name, s in schemes.items():
        headers = {s["name"]: "secret"} if s["type"] == "apiKey" else {"Authorization": "Bearer secret"}
        assert client.get("/api/drafts", headers=headers).status_code == 200, name
