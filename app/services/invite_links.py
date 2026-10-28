"""Named invite links: one link per place the channel is advertised, to see where subscribers come from.

Telegram makes and revokes the links (createChatInviteLink, createChatSubscriptionInviteLink); it never tells a bot
how many joined, so the studio counts the joins (chat_member) and join requests (chat_join_request) itself.
"""

import store
import telegram as tg
from errors import NotFound, StudioError

from . import publisher

NAME_MAX = 32  # Telegram's limit for a link's name
# a paid subscription is always for 30 days (Bot API: subscription_period must be 2592000)
SUBSCRIPTION_PERIOD = 30 * 24 * 3600
MEMBER_LIMIT_MAX = 99999
STARS_MAX = 10000  # Telegram's limit for subscription_price


def _key(chat_id) -> str:
    return str(publisher.resolve_chat(chat_id))


def _name(name: str | None) -> str | None:
    name = (name or "").strip() or None
    if name and len(name) > NAME_MAX:
        raise StudioError(f"название ссылки длиннее {NAME_MAX} символов: столько Telegram не примет")
    return name


def _record(link: dict) -> dict:
    """The columns the studio keeps from Telegram's ChatInviteLink."""
    return {
        "name": link.get("name"),
        "kind": "subscription" if link.get("subscription_price") else "invite",
        "expire_date": link.get("expire_date"),
        "member_limit": link.get("member_limit"),
        "creates_join_request": int(bool(link.get("creates_join_request"))),
        "subscription_period": link.get("subscription_period"),
        "subscription_price": link.get("subscription_price"),
        "is_revoked": int(bool(link.get("is_revoked"))),
    }


def _limits(expire_date: int | None, member_limit, creates_join_request: bool) -> dict:
    """expire_date is already unix time here: a new one is parsed by the caller, a kept one may be in the past."""
    params: dict = {}
    if expire_date:
        params["expire_date"] = expire_date
    if member_limit not in (None, 0):
        if (
            not isinstance(member_limit, int)
            or isinstance(member_limit, bool)
            or not 1 <= member_limit <= MEMBER_LIMIT_MAX
        ):
            raise StudioError(f"member_limit: от 1 до {MEMBER_LIMIT_MAX}")
        if creates_join_request:
            raise StudioError(
                "у ссылки с заявками на вступление не бывает предела участников (так решил Telegram): "
                "снимите предел, member_limit: 0"
            )
        params["member_limit"] = member_limit
    if creates_join_request:
        params["creates_join_request"] = True
    return params


async def create(
    chat_id=None,
    name: str | None = None,
    expire_date=None,
    member_limit: int | None = None,
    creates_join_request: bool = False,
    subscription_price: int | None = None,
    actor: str = "api",
) -> dict:
    """A new named link to the channel. subscription_price (stars a month) makes a paid subscription link instead:
    it has no expiry, limit or join requests."""
    chat = publisher.resolve_chat(chat_id)
    params: dict = {"chat_id": chat}
    if n := _name(name):
        params["name"] = n
    if subscription_price not in (None, 0):
        if expire_date not in (None, "") or member_limit or creates_join_request:
            raise StudioError("у платной подписки нет срока, предела участников и заявок: только название и цена")
        if not isinstance(subscription_price, int) or not 1 <= subscription_price <= STARS_MAX:
            raise StudioError(f"subscription_price: от 1 до {STARS_MAX} звёзд в месяц")
        params.update(subscription_period=SUBSCRIPTION_PERIOD, subscription_price=subscription_price)
        link = await tg.call("createChatSubscriptionInviteLink", params)
    else:
        params.update(_limits(_expire(expire_date), member_limit, creates_join_request))
        link = await tg.call("createChatInviteLink", params)
    return store.invite_links.save(link["invite_link"], str(chat), actor=actor, **_record(link))


def _expire(expire_date) -> int | None:
    """A given expiry: unix or ISO 8601, not in the past; 0 or an empty string means none."""
    return None if expire_date in (None, "", 0) else publisher.parse_at(expire_date)


def _get(invite_link: str) -> dict:
    rec = store.invite_links.get((invite_link or "").strip())
    if not rec:
        raise NotFound(f"ссылки {invite_link} нет среди созданных студией")
    return rec


async def edit(
    invite_link: str,
    name: str | None = None,
    expire_date=None,
    member_limit: int | None = None,
    creates_join_request: bool | None = None,
) -> dict:
    """New name or limits for a link the studio made. What is not given (None) stays as it is; name "",
    expire_date 0 or "", member_limit 0 and creates_join_request false take it off. Telegram drops whatever an
    edit does not repeat, so the studio repeats the rest from the stored link. A subscription changes only its name."""
    rec = _get(invite_link)
    if rec["is_revoked"]:
        raise StudioError("ссылка отозвана: её уже не изменить")
    chat = publisher.resolve_chat(rec["chat_id"])
    kept_name = rec["name"] if name is None else _name(name)
    params: dict = {"chat_id": chat, "invite_link": rec["invite_link"], "name": kept_name or ""}
    if rec["kind"] == "subscription":
        if expire_date not in (None, "", 0) or member_limit or creates_join_request:
            raise StudioError("у платной подписки меняется только название")
        link = await tg.call("editChatSubscriptionInviteLink", params)
    else:
        params.update(
            _limits(
                rec["expire_date"] if expire_date is None else _expire(expire_date),
                rec["member_limit"] if member_limit is None else member_limit,
                bool(rec["creates_join_request"]) if creates_join_request is None else creates_join_request,
            )
        )
        link = await tg.call("editChatInviteLink", params)
    return store.invite_links.save(rec["invite_link"], rec["chat_id"], **_record(link))


async def revoke(invite_link: str) -> dict:
    """The link stops working; the counts stay. The primary link of a channel is not the studio's to revoke."""
    rec = _get(invite_link)
    if rec["is_revoked"]:
        return rec
    link = await tg.call(
        "revokeChatInviteLink", {"chat_id": publisher.resolve_chat(rec["chat_id"]), "invite_link": rec["invite_link"]}
    )
    return store.invite_links.save(rec["invite_link"], rec["chat_id"], **{**_record(link), "is_revoked": 1})


def listing(chat_id=None) -> list[dict]:
    """The links the studio made, newest first, with joined and requests; only one channel when chat_id is set."""
    return store.invite_links.listing(None if chat_id in (None, "") else _key(chat_id))


# a member's status before a join and after it (ChatMember.status); "restricted" counts by is_member
_OUT = ("left", "kicked")
_IN = ("member", "administrator", "creator")


def _inside(m: dict) -> bool:
    return m.get("status") in _IN or (m.get("status") == "restricted" and bool(m.get("is_member")))


def on_member(update: dict) -> bool:
    """chat_member: someone joined through a link the studio made. Leaving is not counted back: Telegram does not
    say which link the person once came through."""
    link = (update.get("invite_link") or {}).get("invite_link")
    old, new = update.get("old_chat_member") or {}, update.get("new_chat_member") or {}
    if not link or _inside(old) or not _inside(new):
        return False
    return store.invite_links.count_join(link)


def on_join_request(request: dict) -> bool:
    """chat_join_request through a link with creates_join_request; the request is for a person to accept."""
    link = (request.get("invite_link") or {}).get("invite_link")
    return store.invite_links.count_request(link) if link else False
