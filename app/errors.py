"""Errors services raise. The API layer turns them into HTTP responses; background loops store them."""

from typing import Any


class StudioError(Exception):
    """A request the studio refuses. `detail` is a message or a dict like {"message", "errors"}."""

    status = 400

    def __init__(self, detail: Any, status: int | None = None):
        super().__init__(detail if isinstance(detail, str) else detail.get("message", str(detail)))
        self.detail = detail
        if status is not None:
            self.status = status


class NotFound(StudioError):
    status = 404


class Invalid(StudioError):
    """The payload did not pass validation against the Bot API spec."""

    status = 422


class Conflict(StudioError):
    status = 409


class Upstream(StudioError):
    """Telegram failed or could not be reached."""

    status = 502


class MayHaveGone(Upstream):
    """A network error during a send: the post may be in the channel. Never retried automatically."""
