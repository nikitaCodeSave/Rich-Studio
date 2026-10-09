"""Uploaded files that posts reference as upload:<name>."""

import asyncio
import base64
import binascii
import ipaddress
import re
import socket
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit

import httpx

import settings
import store
from errors import Conflict, NotFound, StudioError

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
    """Uploads, newest first; `used` is false when no draft and no live post mentions the file."""
    files = [
        p for p in media_dir().iterdir() if p.is_file() and not p.name.startswith(".")
    ]  # .name.part: a download in progress
    files.sort(key=lambda p: -p.stat().st_mtime)
    refs = store.backup.upload_refs()
    return [{**describe(p.name), "size": p.stat().st_size, "used": p.name in refs} for p in files]


def delete_unused(older_than: float | None = None) -> list[str]:
    """Remove files nothing mentions any more (drafts, scheduled, missed and published posts).

    older_than: unix time; only files last changed before it (housekeeping keeps fresh uploads).
    """
    gone = [
        f["name"]
        for f in list_files()
        if not f["used"] and (older_than is None or (media_dir() / f["name"]).stat().st_mtime < older_than)
    ]
    for name in gone:
        path_of(name, must_exist=False).unlink(missing_ok=True)
    return gone


def usage() -> dict:
    """How much the uploads take, and how much of it nothing needs."""
    files = list_files() if media_dir().exists() else []
    unused = [f for f in files if not f["used"]]
    return {
        "files": len(files),
        "bytes": sum(f["size"] for f in files),
        "unused_files": len(unused),
        "unused_bytes": sum(f["size"] for f in unused),
    }


def free_name(filename: str | None) -> str:
    """A safe file name that does not overwrite an existing upload."""
    base = re.sub(r"[^\w.\-]+", "_", Path(filename or "file").name).strip("._") or "file"
    name, i = base, 1
    while (media_dir() / name).exists():
        stem, dot, ext = base.rpartition(".")
        name = f"{stem or base}-{i}{dot}{ext if stem else ''}"
        i += 1
    return name


def used_by(name: str) -> list[str]:
    """What still needs the file: scheduled posts and drafts that mention upload:<name>."""
    ref = describe(name)["ref"]
    posts = [f"отложенный пост в {p['chat_id']}" for p in store.using_upload(ref)]
    return posts + [f"черновик «{d['title'] or 'Без названия'}»" for d in store.drafts_using(ref)]


def delete(name: str, force: bool = False) -> None:
    if not force and (users := used_by(name)):
        raise Conflict({"message": f"файл {name} ещё нужен: " + ", ".join(users), "used_by": users})
    path_of(name, must_exist=False).unlink(missing_ok=True)


def path_of(name: str, must_exist: bool = True) -> Path:
    """The file inside the media folder; never a path outside it."""
    root = media_dir().resolve()
    p = (root / name).resolve()
    if root not in p.parents:
        raise StudioError("Bad Request") if not must_exist else NotFound("Not Found")
    if must_exist and not p.exists():
        raise NotFound("Not Found")
    return p


def _limit() -> int:
    return int(settings.get().max_upload_mb * 1024 * 1024)


def _too_big() -> StudioError:
    return StudioError(
        f"файл больше {settings.get().max_upload_mb:g} МБ: столько Telegram не примет "
        "(для больших файлов нужен свой сервер Bot API, см. TG_API_BASE и MAX_UPLOAD_MB)",
        status=413,
    )


def save_base64(filename: str, data: str) -> dict:
    """An upload an agent sends inline (base64), saved like one from the editor."""
    try:
        raw = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError):
        raise StudioError("data_base64: не base64") from None
    if len(raw) > _limit():
        raise _too_big()
    name = free_name(filename)
    (media_dir() / name).write_bytes(raw)
    return describe(name)


FETCH_REDIRECTS = 5
FETCH_DEADLINE = 300  # seconds for the whole download, however slowly the server answers


async def _resolve(host: str, port: int) -> list[str]:
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        raise StudioError(f"не удалось скачать: адрес {host} не найден") from None
    return [str(addr[0]).split("%")[0] for *_, addr in infos]


async def _public_only(host: str, port: int) -> str | None:
    """The address to connect to: the host resolved once, refused when any of its addresses is the studio itself,
    the local network or another non-public one. None with MEDIA_FETCH_PRIVATE (connect by name as usual)."""
    if settings.get().media_fetch_private:
        return None
    addrs = await _resolve(host, port)
    for a in addrs:
        ip = ipaddress.ip_address(a)
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        if not ip.is_global or ip.is_multicast:
            raise StudioError(
                f"url: {host} — локальный или внутренний адрес, студия туда не ходит "
                "(свой сервер в локальной сети: MEDIA_FETCH_PRIVATE=1)"
            )
    return addrs[0]


def _pinned(url: str, ip: str | None) -> tuple[httpx.URL, dict, dict]:
    """The request goes to the checked address, never to a second DNS answer (DNS rebinding): the URL carries
    the IP, Host and TLS SNI/certificate check keep the name."""
    u = httpx.URL(url)
    if ip is None:
        return u, {}, {}
    ext = {"sni_hostname": u.host} if u.scheme == "https" else {}
    return u.copy_with(host=ip), {"Host": u.netloc.decode("ascii")}, ext


async def fetch(url: str, filename: str | None = None) -> dict:
    """Download a file by http(s) link into the uploads, so a post can use it as upload:<name>.
    Only public addresses, checked again on every redirect; written under a temporary name until complete."""
    name = free_name(filename or unquote(Path(urlsplit(url).path).name) or "file")
    part = media_dir() / f".{name}.part"
    size = 0
    try:
        async with (
            asyncio.timeout(FETCH_DEADLINE),
            httpx.AsyncClient(timeout=60, follow_redirects=False, trust_env=False) as client,
        ):
            for _ in range(FETCH_REDIRECTS + 1):
                parts = urlsplit(url)
                if parts.scheme not in ("http", "https") or not parts.hostname:
                    raise StudioError("url: нужна ссылка http(s)")
                ip = await _public_only(parts.hostname, parts.port or (443 if parts.scheme == "https" else 80))
                target, headers, ext = _pinned(url, ip)
                async with client.stream("GET", target, headers=headers, extensions=ext) as r:
                    if r.is_redirect and r.headers.get("location"):
                        url = urljoin(url, r.headers["location"])
                        continue
                    if r.status_code != 200:
                        raise StudioError(f"не удалось скачать: HTTP {r.status_code}")
                    with part.open("wb") as f:
                        async for chunk in r.aiter_bytes(1 << 20):
                            size += len(chunk)
                            if size > _limit():
                                raise _too_big()
                            f.write(chunk)
                    break
            else:
                raise StudioError(f"не удалось скачать: больше {FETCH_REDIRECTS} перенаправлений")
    except StudioError:
        part.unlink(missing_ok=True)
        raise
    except TimeoutError:
        part.unlink(missing_ok=True)
        raise StudioError(f"не удалось скачать за {FETCH_DEADLINE} с") from None
    except httpx.HTTPError as e:
        part.unlink(missing_ok=True)
        raise StudioError(f"не удалось скачать: {type(e).__name__}") from None
    except BaseException:  # a full disk, a cancelled request: no half-written file stays in the uploads
        part.unlink(missing_ok=True)
        raise
    path = media_dir() / free_name(name)
    part.rename(path)
    return describe(path.name)
