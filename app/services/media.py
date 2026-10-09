"""Uploaded files that posts reference as upload:<name>."""

import re
from pathlib import Path

import settings
from errors import NotFound, StudioError

# served inline; everything else is a download so uploaded html/svg never renders on the studio origin
INLINE = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".gif",
    ".mp4",
    ".mov",
    ".webm",
    ".mp3",
    ".m4a",
    ".ogg",
    ".oga",
    ".opus",
    ".wav",
    ".flac",
}


def media_dir() -> Path:
    return settings.get().media_dir


def kind(name: str) -> str:
    ext = name.rsplit(".", 1)[-1].lower()
    if ext in ("jpg", "jpeg", "png", "webp"):
        return "photo"
    if ext in ("mp4", "mov", "webm", "mkv"):
        return "video"
    if ext == "gif":
        return "animation"
    if ext in ("mp3", "m4a", "flac", "wav"):
        return "audio"
    if ext in ("ogg", "oga", "opus"):
        return "voice_note"
    return "document"


def describe(name: str) -> dict:
    return {"name": name, "kind": kind(name), "url": f"/media/{name}", "ref": f"upload:{name}"}


def list_files() -> list[dict]:
    files = [p for p in media_dir().iterdir() if p.is_file()]
    files.sort(key=lambda p: -p.stat().st_mtime)
    return [{**describe(p.name), "size": p.stat().st_size} for p in files]


def free_name(filename: str | None) -> str:
    """A safe file name that does not overwrite an existing upload."""
    base = re.sub(r"[^\w.\-]+", "_", Path(filename or "file").name).strip("._") or "file"
    name, i = base, 1
    while (media_dir() / name).exists():
        stem, dot, ext = base.rpartition(".")
        name = f"{stem or base}-{i}{dot}{ext if stem else ''}"
        i += 1
    return name


def path_of(name: str, must_exist: bool = True) -> Path:
    """The file inside the media folder; never a path outside it."""
    root = media_dir().resolve()
    p = (root / name).resolve()
    if root not in p.parents:
        raise StudioError("Bad Request") if not must_exist else NotFound("Not Found")
    if must_exist and not p.exists():
        raise NotFound("Not Found")
    return p
