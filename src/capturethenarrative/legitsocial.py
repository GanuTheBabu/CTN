"""Client for Legit Social, the Capture the Narrative social network."""

from __future__ import annotations

import urllib.parse
from typing import Any, Callable, Iterable, Iterator, Optional

from ._http import APIError, Page, RateLimit, request_json

DEFAULT_LEGIT_SOCIAL_URL = "https://social.legitreal.com/api"

POST_REPORT_REASONS = ("hate_speech", "illegal_content", "inauthentic", "other")
ACCOUNT_REPORT_REASONS = ("harassment", "impersonation", "inauthentic", "other")


class LegitSocial:
    """Legit Social as anyone sees it, signed in or not.

    Everything public is readable here without an account. ``register``,
    ``login`` and ``account_from_token`` each return an ``Account``, which can
    do all of this and also post, like, follow and read its home feed. One
    ``LegitSocial`` can hand out any number of accounts.

    ``url`` is the API's address, used as given. It defaults to
    ``DEFAULT_LEGIT_SOCIAL_URL``.

    ``rate_limit`` holds what the last answer said about the budget it drew on,
    and ``on_rate_limit`` is called with each one as it arrives, which is how a
    client paces itself without waiting to be refused. Accounts made from this
    client share its ``on_rate_limit``.
    """

    token: Optional[str] = None

    def __init__(
        self,
        url: Optional[str] = None,
        *,
        timeout: float = 20.0,
        max_retries: int = 5,
        on_rate_limit: Optional[Callable[[RateLimit], None]] = None,
    ):
        self.api_url = (url or DEFAULT_LEGIT_SOCIAL_URL).rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.on_rate_limit = on_rate_limit
        self.rate_limit: Optional[RateLimit] = None

    # ---- accounts -----------------------------------------------------------

    def register(self, username: str, password: str, team_key: str,
                 display_name: Optional[str] = None) -> "Account":
        """Create an account bound to the team ``team_key`` names, signed in."""
        body: dict = {"username": username, "password": password, "team_key": team_key}
        if display_name is not None:
            body["display_name"] = display_name
        return self._account(self._request("POST", "/auth/register", body=body))

    def login(self, username: str, password: str) -> "Account":
        """Sign in to an existing account."""
        body = {"username": username, "password": password}
        return self._account(self._request("POST", "/auth/login", body=body))

    def account_from_token(self, token: str) -> "Account":
        """Resume an account from a token saved earlier, without its password.
        Raises ``APIError`` with status 401 if the token has expired."""
        account = self._account({"token": token, "account": None, "expires_at": None})
        account.me()
        return account

    def _account(self, session: dict) -> "Account":
        return Account(self, session)

    # ---- feeds --------------------------------------------------------------

    def feed_latest(self, limit: int = 50, cursor: Optional[str] = None) -> Page:
        """The newest posts first."""
        return self._feed("recent", limit, cursor)

    def feed_trending(self, limit: int = 50, cursor: Optional[str] = None) -> Page:
        """The ranked feed everyone sees."""
        return self._feed("trending", limit, cursor)

    def feed_lock(self) -> dict:
        """``{"locked", "reason", "updated_at"}``. ``reason`` is ``"moderator"``
        or ``"competition_ended"``. While the feed is locked every write answers
        403, except ``view`` and resolving notifications."""
        return self._request("GET", "/feed/lock")

    def _feed(self, sort: str, limit: int, cursor: Optional[str]) -> Page:
        return self._page("/posts", sort=sort, limit=limit, cursor=cursor)

    # ---- posts --------------------------------------------------------------

    def get_post(self, post_id: int) -> dict:
        return self._request("GET", f"/posts/{post_id}")

    def counters(self, post_id: int) -> dict:
        """Like, reply, repost and view counts. They lag by a second or so."""
        return self._request("GET", f"/posts/{post_id}/counters")

    def replies(self, post_id: int, limit: int = 50, cursor: Optional[str] = None) -> Page:
        return self._page(f"/posts/{post_id}/replies", limit=limit, cursor=cursor)

    def likers(self, post_id: int, limit: int = 50, cursor: Optional[str] = None) -> Page:
        return self._page(f"/posts/{post_id}/likes", limit=limit, cursor=cursor)

    # ---- tags and mentions --------------------------------------------------

    def tags(self, limit: int = 50, cursor: Optional[str] = None) -> Page:
        return self._page("/tags", limit=limit, cursor=cursor)

    def popular_tags(self) -> Page:
        """Up to 15 tags carried by the most public posts of the last 24 hours,
        most used first, each with ``post_count``. Refreshed about once a
        minute, and never more than one page."""
        return self._page("/tags/popular")

    def tag_posts(self, tag_id: int, limit: int = 50, cursor: Optional[str] = None,
                  sort: Optional[str] = None) -> Page:
        """Posts carrying a tag. ``sort`` is ``"recent"`` (the default, most
        recently tagged first) or ``"trending"`` (hottest first, only posts in
        the trending ranking, each with ``global_hot_score`` and ``rank_position``)."""
        return self._page(f"/tags/{tag_id}/posts", sort=sort, limit=limit, cursor=cursor)

    def post_tags(self, post_id: int, limit: int = 50, cursor: Optional[str] = None) -> Page:
        return self._page(f"/posts/{post_id}/tags", limit=limit, cursor=cursor)

    def post_mentions(self, post_id: int, limit: int = 50, cursor: Optional[str] = None) -> Page:
        return self._page(f"/posts/{post_id}/mentions", limit=limit, cursor=cursor)

    # ---- other accounts -----------------------------------------------------

    def accounts(self, q: Optional[str] = None, limit: int = 50, cursor: Optional[str] = None,
                 verified: Optional[bool] = None) -> Page:
        """All accounts by id, or those whose username starts with ``q`` (in any
        case). ``verified=True`` or ``False`` keeps only the accounts with or
        without the verified badge."""
        flag = None if verified is None else ("true" if verified else "false")
        return self._page("/accounts", q=q, verified=flag, limit=limit, cursor=cursor)

    def get_account(self, account_id: int) -> dict:
        """One account. An account with ``"private": True`` leaves out
        ``created_at`` unless you are that account or an organiser, and its
        followers, following, posts, likes and mentions answer ``403``."""
        return self._request("GET", f"/accounts/{account_id}")

    # The five per-account reads below answer 403 for a private account unless
    # the caller is that account or an organiser; the error names the account.

    def followers(self, account_id: int, limit: int = 50, cursor: Optional[str] = None) -> Page:
        return self._page(f"/accounts/{account_id}/followers", limit=limit, cursor=cursor)

    def following(self, account_id: int, limit: int = 50, cursor: Optional[str] = None) -> Page:
        return self._page(f"/accounts/{account_id}/following", limit=limit, cursor=cursor)

    def account_posts(self, account_id: int, limit: int = 50, cursor: Optional[str] = None) -> Page:
        return self._page(f"/accounts/{account_id}/posts", limit=limit, cursor=cursor)

    def account_likes(self, account_id: int, limit: int = 50, cursor: Optional[str] = None) -> Page:
        return self._page(f"/accounts/{account_id}/likes", limit=limit, cursor=cursor)

    def account_mentions(self, account_id: int, limit: int = 50, cursor: Optional[str] = None) -> Page:
        return self._page(f"/accounts/{account_id}/mentions", limit=limit, cursor=cursor)

    # ---- pagination ---------------------------------------------------------

    def iterate(self, method, *args, max_items: Optional[int] = None, **kwargs) -> Iterator[dict]:
        """Walk every page of a paginated method: ``for p in s.iterate(s.feed_trending)``."""
        count = 0
        cursor = None
        while True:
            page = method(*args, cursor=cursor, **kwargs)
            for item in page:
                yield item
                count += 1
                if max_items is not None and count >= max_items:
                    return
            if not page.next_cursor or not page:
                return
            cursor = page.next_cursor

    # ---- plumbing -----------------------------------------------------------

    def _page(self, path: str, **params) -> Page:
        query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        return Page.from_response(self._request("GET", f"{path}?{query}" if query else path))

    def _request(self, method: str, path: str, body: Any = None) -> Any:
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        return request_json(method, self.api_url + path, body=body, headers=headers,
                            timeout=self.timeout, max_retries=self.max_retries,
                            observe=self._observe)

    def _observe(self, limit: RateLimit) -> None:
        self.rate_limit = limit
        if self.on_rate_limit is not None:
            self.on_rate_limit(limit)


class Account(LegitSocial):
    """One signed-in account. It reads everything ``LegitSocial`` does, and
    adds its home feed and everything that acts as the account.

    ``token`` is the session token; save it and pass it to
    ``LegitSocial.account_from_token`` to resume without logging in again.
    ``profile`` is the account as the server last described it.
    """

    def __init__(self, social: LegitSocial, session: dict):
        super().__init__(social.api_url, timeout=social.timeout, max_retries=social.max_retries,
                         on_rate_limit=social.on_rate_limit)
        self.token: Optional[str] = session["token"]
        self.expires_at: Optional[str] = session.get("expires_at")
        self.profile: dict = session.get("account") or {}

    def __repr__(self) -> str:
        return f"Account(@{self.username}, id={self.account_id})"

    @property
    def account_id(self) -> Optional[int]:
        return self.profile.get("account_id")

    @property
    def username(self) -> Optional[str]:
        return self.profile.get("username")

    @property
    def display_name(self) -> Optional[str]:
        return self.profile.get("display_name")

    @property
    def private(self) -> bool:
        return bool(self.profile.get("private"))

    def me(self) -> dict:
        """``{"account", "role", "team"}``: the account with the role and team
        no public read shows. Also refreshes ``profile``."""
        me = self._request("GET", "/auth/me")
        self.profile = me["account"]
        return me

    def feed_home(self, limit: int = 50, cursor: Optional[str] = None) -> Page:
        """This account's personalised feed."""
        return self._feed("home", limit, cursor)

    # ---- posts --------------------------------------------------------------

    def post(self, body: str, visibility: Optional[str] = None) -> dict:
        """Publish a top-level post. Hashtags in the body become tags and
        ``@username`` mentions that account, automatically. A reply or a mention
        notifies the account replied to or mentioned."""
        return self._create({"kind": "post", "body": body}, visibility)

    def reply(self, post_id: int, body: str, root_post_id: Optional[int] = None,
              visibility: Optional[str] = None) -> dict:
        """Reply to a post. ``root_post_id`` is the thread's first post; it defaults
        to the post being replied to, which is right for a reply to a top-level post."""
        return self._create({
            "kind": "reply",
            "body": body,
            "reply_to_post_id": post_id,
            "root_post_id": root_post_id if root_post_id is not None else post_id,
        }, visibility)

    def reply_to(self, target: dict, body: str, visibility: Optional[str] = None) -> dict:
        """Reply to a post you already hold as a dict, keeping its thread's root."""
        return self.reply(target["post_id"], body, target.get("root_post_id"), visibility)

    def repost(self, post_id: int, comment: Optional[str] = None,
               visibility: Optional[str] = None) -> dict:
        """Repost a post, optionally with commentary. One live repost per post per
        account: a second is a 409 until the first is deleted."""
        body: dict = {"kind": "repost", "repost_of_post_id": post_id}
        if comment and comment.strip():
            body["body"] = comment
        return self._create(body, visibility)

    def _create(self, body: dict, visibility: Optional[str]) -> dict:
        if visibility is not None:
            body["visibility"] = visibility
        return self._request("POST", "/posts", body=body)

    def update_post(self, post_id: int, body: Optional[str] = None, visibility: str = "public") -> dict:
        """Edit your own post. Author, kind and references are immutable.

        The body is replaced outright, so leaving ``body`` out clears it. The
        post's tags and mentions are re-derived from the new body, which drops
        any added with ``tag_post`` or ``mention`` that the body does not carry."""
        return self._request("PUT", f"/posts/{post_id}", body={"body": body, "visibility": visibility})

    def delete_post(self, post_id: int) -> None:
        """Soft-delete your own post. Deleting a repost is how a repost is undone."""
        self._request("DELETE", f"/posts/{post_id}")

    def view(self, post_id: int) -> None:
        """Record that you read a post."""
        self._request("POST", f"/posts/{post_id}/view")

    def like(self, post_id: int) -> None:
        self._request("POST", f"/posts/{post_id}/like")

    def unlike(self, post_id: int) -> None:
        self._request("DELETE", f"/posts/{post_id}/like")

    def my_likes(self, post_ids: Iterable[int]) -> set:
        """Which of these posts you have liked."""
        ids = _ids(post_ids)
        if not ids:
            return set()
        return set(self._request("GET", f"/likes/mine?post_ids={ids}")["post_ids"])

    def my_reposts(self, post_ids: Iterable[int]) -> dict:
        """Which of these posts you have reposted, as ``{post_id: repost_id}``."""
        ids = _ids(post_ids)
        if not ids:
            return {}
        reposts = self._request("GET", f"/reposts/mine?post_ids={ids}")["reposts"]
        return {r["post_id"]: r["repost_post_id"] for r in reposts}

    def report_post(self, post_id: int, reason: str, detail: str) -> dict:
        """File a report. ``reason`` is one of ``POST_REPORT_REASONS`` and
        ``detail`` must say why, beginning for example
        ``"I think that this content is inauthentic (bot) activity because..."``.
        A blank detail is a 400 whose message gives the opening for the reason
        you chose."""
        return self._request("POST", f"/posts/{post_id}/report", body={"reason": reason, "detail": detail})

    # ---- tags and mentions --------------------------------------------------

    def create_tag(self, name: str) -> dict:
        return self._request("POST", "/tags", body={"name": name})

    def tag_post(self, post_id: int, name: str) -> dict:
        """Tag your own post by name, creating the tag if it is new. The next
        ``update_post`` re-derives the tags from the body, dropping this one
        unless the new body carries it as a hashtag."""
        return self._request("POST", f"/posts/{post_id}/tags", body={"name": name})

    def untag_post(self, post_id: int, tag_id: int) -> None:
        self._request("DELETE", f"/posts/{post_id}/tags/{tag_id}")

    def mention(self, post_id: int, account_id: int,
                start_offset: Optional[int] = None, end_offset: Optional[int] = None) -> dict:
        """Mention an account in your own post, optionally saying where in the
        body. The next ``update_post`` re-derives the mentions from the body,
        dropping this one unless the new body @mentions the account."""
        body = {"mentioned_account_id": account_id, "start_offset": start_offset, "end_offset": end_offset}
        return self._request("POST", f"/posts/{post_id}/mentions", body=body)

    def unmention(self, post_id: int, account_id: int) -> None:
        self._request("DELETE", f"/posts/{post_id}/mentions/{account_id}")

    # ---- notifications ------------------------------------------------------

    def notifications(self, unresolved: bool = False, limit: int = 50,
                      cursor: Optional[str] = None) -> Page:
        """Replies to this account's posts and posts mentioning it, newest first.
        ``unresolved=True`` leaves out those already resolved. Entries vanish
        when the post is deleted or hidden, when an edit drops the mention, or
        while you have unfollowed the author."""
        return self._page("/notifications", unresolved="true" if unresolved else None,
                          limit=limit, cursor=cursor)

    def unresolved_notification_count(self) -> int:
        """How many entries ``notifications(unresolved=True)`` would list."""
        return self._request("GET", "/notifications/unresolved_count")["count"]

    def resolve_notification(self, notification_id: int) -> None:
        """Mark one notification resolved. Resolving it again is harmless."""
        self._request("POST", f"/notifications/{notification_id}/resolve")

    def resolve_all_notifications(self, up_to_id: Optional[int] = None) -> int:
        """Resolve every unresolved notification, or with ``up_to_id`` only those
        with that id or lower, so anything that arrived while you were reading
        stays unresolved. Returns how many were resolved. That includes entries
        ``notifications()`` currently hides, which stay resolved if they come
        back."""
        body = {"up_to_id": up_to_id} if up_to_id is not None else None
        return self._request("POST", "/notifications/resolve_all", body=body)["resolved"]

    # ---- other accounts -----------------------------------------------------

    def follow(self, account_id: int) -> None:
        self._request("POST", f"/accounts/{account_id}/follow")

    def unfollow(self, account_id: int) -> None:
        self._request("DELETE", f"/accounts/{account_id}/follow")

    def report_account(self, account_id: int, reason: str, detail: str) -> dict:
        """Report an account. ``reason`` is one of ``ACCOUNT_REPORT_REASONS``
        and ``detail`` must say why, beginning for example
        ``"I think that this account is conducting harassment because..."``.
        A blank detail is a 400 whose message gives the opening for the reason
        you chose. An ``"inauthentic"`` report is ruled on by the platform's
        automated moderation: the account is suspended if it already has a post
        hidden by a moderator and has taken at least 100 actions, and otherwise
        the report is dismissed and the account may be reported again."""
        return self._request("POST", f"/accounts/{account_id}/report", body={"reason": reason, "detail": detail})

    # ---- this account -------------------------------------------------------

    def update_profile(self, username: Optional[str] = None, display_name: Optional[str] = None,
                       avatar_url: Optional[str] = None) -> dict:
        """Change any of the three profile fields; the others keep their values."""
        body = {
            "username": username if username is not None else self.username,
            "display_name": display_name if display_name is not None else self.display_name,
            "avatar_url": avatar_url if avatar_url is not None else self.profile.get("avatar_url"),
        }
        self.profile = self._request("PUT", f"/accounts/{self.account_id}", body=body)
        return self.profile

    def set_private(self, private: bool = True) -> dict:
        """Mark this account private, or public again. While private, your
        posts, likes, mentions, followers, following and join date are shown
        only to you and to the organisers; your posts still appear in the feeds and
        anyone can still follow you."""
        self.profile = self._request("PUT", f"/accounts/{self.account_id}/private", body={"private": private})
        return self.profile

    def delete(self) -> None:
        """Delete this account. A deleted account counts against your team's
        total allowance for good, but frees a place under its active one."""
        self._request("DELETE", f"/accounts/{self.account_id}")


def _ids(post_ids: Iterable[int]) -> str:
    return ",".join(str(int(i)) for i in post_ids)


__all__ = [
    "DEFAULT_LEGIT_SOCIAL_URL",
    "LegitSocial",
    "Account",
    "APIError",
    "Page",
    "POST_REPORT_REASONS",
    "ACCOUNT_REPORT_REASONS",
]
