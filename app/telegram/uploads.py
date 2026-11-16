"""Turn "upload:<name>" media references into multipart attachments."""

import re
from pathlib import Path
from typing import Any


class MissingUpload(Exception):
    pass


# InputFile fields: `media` in blocks, InputMedia and InputPaidMedia; the rest name files of Live Photo and video
FILE_KEYS = {"media", "photo", "live_photo", "thumbnail", "cover"}
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def name_error(name: str) -> str | None:
    """Why the name after upload: is not the name of an upload, None when it is one: a plain file name, as the studio
    makes them (services.media.free_name), never a path. Only the name: whether the file is there is find()'s (an
    empty name is no file either). Taken as it is, never URL-decoded: «%2e%2e» is a name, encoded again in a URL."""
    if "/" in name or "\\" in name:
        return "это путь"
    if name.startswith("."):
        return "имя начинается с точки (.. или скрытый файл)"
    if _CONTROL.search(name):
        return "в имени управляющий символ"
    return None


def _check_name(name: str) -> None:
    if why := name_error(name):
        raise MissingUpload(f"upload:{name!r}: {why}, а нужно имя загруженного файла (GET /api/media)")


def find(name: str, media_dir: Path, must_exist: bool = True) -> Path:
    """The file of upload:<name> inside the media folder, never a path outside it; MissingUpload when the name is not
    one of an upload (name_error) or, with must_exist, there is no such file. Nothing is asked of Telegram before."""
    _check_name(name)
    try:
        root = media_dir.resolve()
        path = (root / name).resolve()
        if root not in path.parents or (must_exist and not path.is_file()):
            raise MissingUpload(f"файл {name!r} не найден в загрузках")
    except (OSError, ValueError):  # a name too long for the disk: no file of the uploads either
        raise MissingUpload(f"файл {name!r} не найден в загрузках") from None
    return path


def collect_uploads(obj: Any, files: dict[str, Path], media_dir: Path, key: str = "", must_exist: bool = True) -> Any:
    """Replace "upload:<name>" in file fields (FILE_KEYS) with attach://fN and remember the files.

    Only string values of file fields are rewritten, so post text that happens to start with "upload:" is left alone
    (a `photo` block is an object, not a string, and is walked into as usual).
    The same file referenced several times is attached once. must_exist=False (a draft: the file may be uploaded
    later): only the names are checked, and nothing is rewritten.
    """
    if isinstance(obj, dict):
        return {k: collect_uploads(v, files, media_dir, k, must_exist) for k, v in obj.items()}
    if isinstance(obj, list):
        return [collect_uploads(v, files, media_dir, key, must_exist) for v in obj]
    if key in FILE_KEYS and isinstance(obj, str) and obj.startswith("upload:"):
        name = obj[len("upload:") :]
        if not must_exist:
            _check_name(name)
            return obj
        path = find(name, media_dir)
        for k, p in files.items():
            if p == path:
                return f"attach://{k}"
        k = f"f{len(files)}"
        files[k] = path
        return f"attach://{k}"
    return obj
