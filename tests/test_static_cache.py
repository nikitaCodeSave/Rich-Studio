import re


def test_vendored_codemirror_says_how_to_cache_it(client):
    # the module is checked on every load; the directory it points at is named after its content and never changes
    entry = client.get("/static/vendor/codemirror.js")
    assert entry.status_code == 200 and entry.headers["cache-control"] == "no-cache"
    found = re.search(r"\./(codemirror-[0-9a-f]+)/state\.js", entry.text)
    assert found
    file = client.get(f"/static/vendor/{found.group(1)}/state.js")
    assert file.status_code == 200 and file.headers["cache-control"] == "public, max-age=31536000, immutable"
    again = client.get("/static/vendor/codemirror.js", headers={"if-none-match": entry.headers["etag"]})
    assert again.status_code == 304 and again.headers["cache-control"] == "no-cache"
    # the rest of the static files as before
    assert "cache-control" not in client.get("/static/js/main.js").headers
    assert client.get(f"/static/vendor/{found.group(1)}/nope.js").status_code == 404
