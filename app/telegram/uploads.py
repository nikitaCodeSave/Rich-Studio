"""Turn "upload:<name>" media references into multipart attachments."""

from pathlib import Path
from typing import Any


class MissingUpload(Exception):
    pass


# InputFile fields: `media` in blocks, InputMedia and InputPaidMedia; the rest name files of Live Photo and video
FILE_KEYS = {"media", "photo", "live_photo", "thumbnail", "cover"}


def collect_uploads(obj: Any, files: dict[str, Path], media_dir: Path, key: str = "") -> Any:
    """Replace "upload:<name>" in file fields (FILE_KEYS) with attach://fN and remember the files.

    Only string values of file fields are rewritten, so post text that happens to start with "upload:" is left alone
    (a `photo` block is an object, not a string, and is walked into as usual).
    The same file referenced several times is attached once.
    """
    if isinstance(obj, dict):
        return {k: collect_uploads(v, files, media_dir, k) for k, v in obj.items()}
    if isinstance(obj, list):
        return [collect_uploads(v, files, media_dir, key) for v in obj]
    if key in FILE_KEYS and isinstance(obj, str) and obj.startswith("upload:"):
        name = obj[len("upload:") :]
        path = (media_dir / name).resolve()
        if media_dir.resolve() not in path.parents or not path.exists():
            raise MissingUpload(f"файл {name!r} не найден в загрузках")
        for k, p in files.items():
            if p == path:
                return f"attach://{k}"
        k = f"f{len(files)}"
        files[k] = path
        return f"attach://{k}"
    return obj
