"""Drafts written by an agent: the studio's own notes on a draft survive the replacement of the post."""

# the editor's copies of the post (they describe the post being replaced) and its mode: not notes
EDITOR_KEYS = ("buffers", "json", "format")


def keep_notes(post: dict, old_payload: dict | None) -> dict:
    """The post to save over a draft: the `_studio` notes of the old payload (`source`, `warnings` of a forwarded
    post and any other mark of origin) stay unless `post` brings its own `_studio` (the editor does, whole);
    the editor's buffers, unapplied JSON and mode are dropped: they describe the post being replaced."""
    if "_studio" in post or not isinstance(old_payload, dict):
        return post
    old = old_payload.get("_studio")
    notes = {k: v for k, v in old.items() if k not in EDITOR_KEYS} if isinstance(old, dict) else {}
    return {**post, "_studio": notes} if notes else post
