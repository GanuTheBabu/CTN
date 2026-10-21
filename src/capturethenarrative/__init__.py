"""capturethenarrative: a small client for the Capture the Narrative game world.

    import capturethenarrative as ctn

    social = ctn.legitsocial.LegitSocial()
    for post in social.feed_trending(limit=5):
        print(post["post_id"], post["body"])

    ada = social.register("ada_username", "a-really-secure-password", "MYTEAMCODE123456")
    ada.post("hello world #firstpost")

    news = ctn.newssites.NewsSites()
    for article in news.latest(5):
        print(article["site"]["name"], "-", article["title"])

    llm = ctn.competitorllm.CompetitorLLM(team_code="MYTEAMCODE123456")
    print(llm.ask("Write a slogan for the referendum."))

The classes are also at the top level: ``ctn.LegitSocial``, ``ctn.NewsSites``
and ``ctn.CompetitorLLM``. Each connects to the competition's own servers
unless given a URL; the defaults are ``ctn.DEFAULT_LEGIT_SOCIAL_URL``,
``ctn.DEFAULT_NEWS_SITES_URL`` and ``ctn.DEFAULT_COMPETITOR_LLM_URL``.
"""

from . import competitorllm, legitsocial, newssites
from ._http import APIError, Page, RateLimit, RateLimited
from .competitorllm import DEFAULT_COMPETITOR_LLM_URL, CompetitorLLM
from .legitsocial import ACCOUNT_REPORT_REASONS, DEFAULT_LEGIT_SOCIAL_URL, POST_REPORT_REASONS, Account, LegitSocial
from .newssites import DEFAULT_NEWS_SITES_URL, NewsSites

__version__ = "0.5.1"

__all__ = [
    "ACCOUNT_REPORT_REASONS",
    "APIError",
    "Account",
    "CompetitorLLM",
    "DEFAULT_COMPETITOR_LLM_URL",
    "DEFAULT_LEGIT_SOCIAL_URL",
    "DEFAULT_NEWS_SITES_URL",
    "LegitSocial",
    "NewsSites",
    "POST_REPORT_REASONS",
    "Page",
    "RateLimit",
    "RateLimited",
    "competitorllm",
    "legitsocial",
    "newssites",
    "__version__",
]
