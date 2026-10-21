"""The one HTTP helper both clients use: JSON in, JSON out, errors as exceptions."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Optional


class RateLimit:
    """What the ``X-RateLimit-*`` headers said about the budget a request drew on.

    The server checks every bucket a route belongs to and describes whichever
    binds hardest, so ``remaining`` is the number of further requests that route
    allows and ``reset`` the seconds until the budget is whole again. The budget
    refills steadily rather than all at once, so when it is spent, one request's
    worth of it comes back in about ``reset / limit`` seconds.
    """

    __slots__ = ("limit", "remaining", "reset")

    def __init__(self, limit: int, remaining: int, reset: int):
        self.limit = limit
        self.remaining = remaining
        self.reset = reset

    @classmethod
    def from_headers(cls, headers) -> Optional["RateLimit"]:
        try:
            return cls(
                int(headers["X-RateLimit-Limit"]),
                int(headers["X-RateLimit-Remaining"]),
                int(headers["X-RateLimit-Reset"]),
            )
        except (KeyError, TypeError, ValueError):
            return None

    def __repr__(self) -> str:
        return f"RateLimit(limit={self.limit}, remaining={self.remaining}, reset={self.reset})"


class APIError(Exception):
    """A non-2xx answer from the game world.

    ``status`` is the HTTP status, ``message`` the server's own one-line
    explanation when it gave one, and ``json`` the decoded error body (an empty
    dict when the body was not JSON).
    """

    def __init__(self, method: str, url: str, status: int, body: str,
                 rate_limit: Optional[RateLimit] = None):
        self.method = method
        self.url = url
        self.status = status
        self.body = body
        self.rate_limit = rate_limit
        try:
            decoded = json.loads(body) if body.strip() else {}
        except ValueError:
            decoded = {}
        self.json: dict = decoded if isinstance(decoded, dict) else {}
        self.message = str(self.json.get("error") or body.strip()[:200] or f"HTTP {status}")
        super().__init__(f"{method} {url} -> {status}: {self.message}")

    @property
    def not_ready_reasons(self) -> Optional[list]:
        """The accounts system's reasons when a write was refused because the team
        is not ready to compete, or a registration because the competition has
        not started yet, otherwise ``None``."""
        reasons = self.json.get("not_ready_reasons")
        return list(reasons) if isinstance(reasons, list) else None


class RateLimited(APIError):
    """A 429 that was still a 429 after every retry. ``retry_after`` is in seconds."""

    def __init__(self, method: str, url: str, status: int, body: str, retry_after: int,
                 rate_limit: Optional[RateLimit] = None):
        super().__init__(method, url, status, body, rate_limit)
        self.retry_after = retry_after


class Page(list):
    """One page of a cursor-paginated list.

    It is a plain list of the items, with the opaque ``next_cursor`` to pass
    back for the following page hanging off it (``None`` on the last page).
    """

    def __init__(self, items, next_cursor: Optional[str] = None):
        super().__init__(items)
        self.next_cursor = next_cursor

    @classmethod
    def from_response(cls, data: Any) -> "Page":
        if isinstance(data, dict) and "items" in data:
            return cls(data["items"] or [], data.get("next_cursor"))
        return cls(data or [])


def request_json(
    method: str,
    url: str,
    *,
    body: Any = None,
    headers: Optional[dict] = None,
    timeout: float = 20.0,
    max_retries: int = 5,
    observe: Optional[Callable[[RateLimit], None]] = None,
) -> Any:
    """Send one JSON request and return the decoded response (``None`` when empty).

    A 429 is slept through for the server's ``Retry-After`` and retried up to
    ``max_retries`` times before it becomes a ``RateLimited``. Every other
    non-2xx status is an ``APIError``. ``observe`` is handed the
    ``RateLimit`` from every answer that carries the headers, including each
    retried 429.
    """
    data = json.dumps(body).encode() if body is not None else None
    sent_headers = {"Accept": "application/json"}
    if data is not None:
        sent_headers["Content-Type"] = "application/json"
    sent_headers.update(headers or {})

    for attempt in range(max_retries + 1):
        req = urllib.request.Request(url, data=data, method=method, headers=sent_headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                limit = RateLimit.from_headers(resp.headers)
                raw = resp.read().decode()
        except urllib.error.HTTPError as e:
            limit = RateLimit.from_headers(e.headers)
            raw = e.read().decode(errors="replace")
            if observe is not None and limit is not None:
                observe(limit)
            if e.code == 429:
                wait = _retry_after(e.headers.get("Retry-After"))
                if attempt < max_retries:
                    time.sleep(wait)
                    continue
                raise RateLimited(method, url, e.code, raw, wait, limit) from None
            raise APIError(method, url, e.code, raw, limit) from None
        if observe is not None and limit is not None:
            observe(limit)
        return json.loads(raw) if raw.strip() else None


def _retry_after(header: Optional[str]) -> int:
    try:
        secs = int(header or "")
    except ValueError:
        return 10
    return max(1, min(secs, 120))
