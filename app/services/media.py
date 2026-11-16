"""Uploaded files that posts reference as upload:<name>."""

import asyncio
import base64
import binascii
import ipaddress
import os
import re
import secrets
import socket
import stat
import urllib.request
from pathlib import Path
from urllib.parse import quote, unquote, urljoin, urlsplit

import httpx

import settings
import store
from errors import Conflict, NotFound, StudioError
from telegram import uploads

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


def shown(name: str) -> str:
    """The name for JSON (answers, the log, a search in the database): one that is not UTF-8 (after an unzip, by
    hand; Python keeps it with surrogates) has «�» in place of its bytes. Its URL (_url) keeps the bytes."""
    return name.encode("utf-8", "surrogateescape").decode("utf-8", "replace")


def describe(name: str) -> dict:
    return {"name": shown(name), "kind": kind(name), "url": _url(name), "ref": f"upload:{shown(name)}"}


def _files() -> list[tuple[Path, os.stat_result, bool]]:
    """The files of the uploads, newest first, each looked at once (a file deleted meanwhile, say by the DELETE the
    editor reads the list after, is left out) and with whether a draft, template or live post mentions it."""
    files = []
    for p in media_dir().iterdir():
        if p.name.startswith("."):  # .name.part: a download in progress
            continue
        try:
            st = p.stat()
        except OSError:  # gone meanwhile, a symlink loop (ELOOP), no access: not a file to list
            continue
        if stat.S_ISREG(st.st_mode):
            files.append((p, st))
    files.sort(key=lambda f: -f[1].st_mtime)
    refs = store.backup.upload_refs()
    return [(p, st, p.name in refs) for p, st in files]


def list_files() -> list[dict]:
    """Uploads, newest first; `used` is false when no draft, template or live post mentions the file."""
    return [{**describe(p.name), "size": st.st_size, "used": used} for p, st, used in _files()]


def delete_unused(older_than: float | None = None) -> list[str]:
    """Remove files nothing mentions any more (drafts, templates, scheduled, missed and published posts).

    older_than: unix time; only files last changed before it (housekeeping keeps fresh uploads).
    """
    gone = [p for p, st, used in _files() if not used and (older_than is None or st.st_mtime < older_than)]
    for p in gone:  # the folder's own files: also one put there by hand that no post can use (path_of)
        p.unlink(missing_ok=True)
    return [shown(p.name) for p in gone]


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
    """What still needs the file: scheduled or missed posts, drafts and templates that mention upload:<name>. The
    stored content is UTF-8: a name that is not (shown with «�») is mentioned by none of it."""
    ref = describe(name)["ref"]
    waiting = {"scheduled": "отложенный", "missed": "пропущенный"}
    posts = [f"{waiting[p['status']]} пост в {p['chat_id']}" for p in store.using_upload(ref)]
    drafts = [f"черновик «{d['title'] or 'Без названия'}»" for d in store.drafts_using(ref)]
    return posts + drafts + [f"шаблон «{t['title']}»" for t in store.templates.templates_using(ref)]


def delete(name: str, force: bool = False) -> None:
    if not force and (users := used_by(name)):
        raise Conflict({"message": f"файл {name} ещё нужен: " + ", ".join(users), "used_by": users})
    path_of(name, must_exist=False).unlink(missing_ok=True)


def path_of(name: str, must_exist: bool = True) -> Path:
    """The file inside the media folder; never a path outside it, nor a name that is not one of an upload
    (telegram.uploads.find: the same check as a send)."""
    try:
        return uploads.find(name, media_dir(), must_exist)
    except uploads.MissingUpload:
        raise (NotFound("Not Found") if must_exist else StudioError("Bad Request")) from None


def url_of(name: str) -> str | None:
    """/media/<name> for the editor's <img>, the name encoded whole; None unless it is a file of the uploads, so no
    stored name sends the browser anywhere but to /media/."""
    try:
        path_of(name)
    except StudioError:
        return None
    return _url(name)


def _url(name: str) -> str:
    # a name that is not UTF-8 (after an unzip, by hand) is kept with surrogates: its URL is its bytes as they are
    return "/media/" + quote(name, safe="", errors="surrogateescape")


def check_refs(post: dict, must_exist: bool = True) -> None:
    """Every upload:<name> in a file field of `post` (as a send finds them, telegram.uploads) names a file of the
    uploads: refused as a send refuses it (StudioError, 400) before anything is written or asked of Telegram.
    must_exist=False (a draft, a template: the file may be uploaded later): only a name that is a path is refused."""
    try:
        uploads.collect_uploads(post, {}, media_dir(), must_exist=must_exist)
    except uploads.MissingUpload as e:
        raise StudioError(str(e)) from None


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


# names that are local by convention (RFC 6761/6762/8375 and common private zones): never the public internet,
# whatever a resolver or a proxy would make of them
INTERNAL_SUFFIXES = (
    ".localhost",
    ".local",
    ".internal",
    ".intranet",
    ".lan",
    ".home",
    ".home.arpa",
    ".corp",
    ".private",
)


def _internal_name(host: str) -> bool:
    """A name without a dot (intranet, localhost) or with a local suffix (db.corp, metadata.google.internal)."""
    try:
        ipaddress.ip_address(host)
        return False  # an address is checked as an address
    except ValueError:
        pass
    name = host.lower().rstrip(".")
    return "." not in name or name.endswith(INTERNAL_SUFFIXES)


def _refuse(host: str) -> StudioError:
    return StudioError(
        f"url: {host} — локальный или внутренний адрес, студия туда не ходит "
        "(свой сервер в локальной сети: MEDIA_FETCH_PRIVATE=1)"
    )


async def _public_only(host: str, port: int, proxied: bool = False) -> str | None:
    """The address to connect to: the host resolved once, refused when any of its addresses is the studio itself,
    the local network or another non-public one; a local name (_internal_name) is refused before any lookup.
    None with MEDIA_FETCH_PRIVATE (connect by name as usual), or when a public-looking name behind a proxy does not
    resolve here (a network where only the proxy sees the internet)."""
    if settings.get().media_fetch_private:
        return None
    # before any lookup: behind a proxy an unresolved name goes to the proxy, which may well reach the inside
    if _internal_name(host):
        raise _refuse(host)
    try:
        addrs = await _resolve(host, port)
    except StudioError:
        if proxied:
            return None
        raise
    for a in addrs:
        ip = ipaddress.ip_address(a)
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        if not ip.is_global or ip.is_multicast:
            raise _refuse(host)
    # IPv4 first: a Docker container often has no IPv6 route, while the host has both
    return sorted(addrs, key=lambda a: ":" in a)[0]


def _pinned(url: str, ip: str | None) -> tuple[httpx.URL, dict, dict]:
    """The request goes to the checked address, never to a second DNS answer (DNS rebinding): the URL carries
    the IP, Host and TLS SNI/certificate check keep the name."""
    u = httpx.URL(url)
    if ip is None:
        return u, {}, {}
    ext = {"sni_hostname": u.host} if u.scheme == "https" else {}
    return u.copy_with(host=ip), {"Host": u.netloc.decode("ascii")}, ext


def _proxy_for(scheme: str, host: str) -> str | None:
    """The proxy the system settings give for this link (HTTPS_PROXY, HTTP_PROXY, ALL_PROXY, NO_PROXY), the same
    one the requests to Telegram go through; None: connect directly."""
    proxies = urllib.request.getproxies_environment()
    proxy = proxies.get(scheme) or proxies.get("all")
    if not proxy or urllib.request.proxy_bypass(host):
        return None
    return proxy


# like a browser's download, not "python-httpx": some file hosts (Wikimedia among them) refuse an unnamed client
USER_AGENT = f"RichStudio/{settings.VERSION} (media fetch)"


async def fetch(url: str, filename: str | None = None) -> dict:
    """Download a file by http(s) link into the uploads, so a post can use it as upload:<name>.
    Only public addresses, checked again on every redirect; written under a temporary name until complete.
    Through the system proxy when one is set for the link: then the proxy connects by name, so the address check
    can not be pinned to the connection (the proxy is trusted to reach only what it should)."""
    name = free_name(filename or unquote(Path(urlsplit(url).path).name) or "file")
    part = media_dir() / f".{name}.{secrets.token_hex(4)}.part"  # two downloads of one name never share it
    size = 0
    # certificates as for Telegram: SSL_CERT_FILE/SSL_CERT_DIR when set (a TLS-inspecting proxy), else certifi
    tls = httpx.create_ssl_context(trust_env=True)
    try:
        async with asyncio.timeout(FETCH_DEADLINE):
            for _ in range(FETCH_REDIRECTS + 1):
                parts = urlsplit(url)
                if parts.scheme not in ("http", "https") or not parts.hostname:
                    raise StudioError("url: нужна ссылка http(s)")
                proxy = _proxy_for(parts.scheme, parts.hostname)
                port = parts.port or (443 if parts.scheme == "https" else 80)
                ip = await _public_only(parts.hostname, port, proxied=proxy is not None)
                target, headers, ext = _pinned(url, None if proxy else ip)
                async with (
                    httpx.AsyncClient(
                        timeout=60, follow_redirects=False, trust_env=False, verify=tls, proxy=proxy
                    ) as client,
                    client.stream("GET", target, headers={"User-Agent": USER_AGENT, **headers}, extensions=ext) as r,
                ):
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
