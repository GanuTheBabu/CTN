"""Reader for the in-universe news and campaign websites.

Every page on those sites is also a JSON document, reached by putting ``.json``
on the end of its URL. This module does that for you. No key is needed: these
are public pages.
"""

from __future__ import annotations

from typing import Optional, Union

from ._http import request_json

DEFAULT_NEWS_SITES_URL = "https://sites.legitreal.com"

Site = Union[str, dict]


class NewsSites:
    """The publications served by the news sites host.

    ``url`` is the host's address, used as given. It defaults to
    ``DEFAULT_NEWS_SITES_URL``.

    A ``site`` argument is a publication's ``sub_url`` (``"kingstonia-herald"``)
    or a site dict from ``sites()``.
    """

    def __init__(self, url: Optional[str] = None, *, timeout: float = 20.0, max_retries: int = 5):
        self.url = (url or DEFAULT_NEWS_SITES_URL).rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries

    def sites(self) -> list:
        """Every active publication: id, name, sub_url, url, tagline, about, logo_url."""
        return self._doc("/index.json")["sites"]

    def front_page(self, site: Site) -> dict:
        """A publication's front page: ``{"site", "categories", "articles"}``.
        Articles carry headline, summary, byline and URL but not the body."""
        return self._doc(f"/{_sub(site)}.json")

    def articles(self, site: Site) -> list:
        """The recent pieces on a publication's front page, newest first."""
        return self.front_page(site)["articles"]

    def article(self, site: Site, slug: str) -> dict:
        """One piece in full, its markdown ``body`` included."""
        return self._doc(f"/{_sub(site)}/articles/{slug}.json")["article"]

    def author(self, site: Site, username: str) -> dict:
        """A byline and what it has published: ``{"site", "author", "articles"}``."""
        return self._doc(f"/{_sub(site)}/authors/{username}.json")

    def category(self, site: Site, slug: str) -> dict:
        """One section of a publication: ``{"site", "category", "articles"}``."""
        return self._doc(f"/{_sub(site)}/categories/{slug}.json")

    def latest(self, limit: Optional[int] = 20) -> list:
        """The newest pieces across every publication, each with a ``site`` key
        added so you know where it ran."""
        items = []
        for site in self.sites():
            for article in self.articles(site):
                items.append({**article, "site": site})
        items.sort(key=lambda a: a.get("published_at", ""), reverse=True)
        return items[:limit] if limit is not None else items

    def _doc(self, path: str) -> dict:
        return request_json("GET", self.url + path, timeout=self.timeout, max_retries=self.max_retries)


def _sub(site: Site) -> str:
    if isinstance(site, dict):
        return site["sub_url"]
    return site.strip("/")


__all__ = ["DEFAULT_NEWS_SITES_URL", "NewsSites"]
