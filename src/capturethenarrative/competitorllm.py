"""Client for the competition LLM, an OpenAI-compatible endpoint keyed by team code."""

from __future__ import annotations

from typing import Any, Optional

import openai

DEFAULT_COMPETITOR_LLM_URL = "https://llm.legitreal.com/v1"

MODEL = "qwen2.5-3b-instruct"


class CompetitorLLM(openai.OpenAI):
    """The stock ``openai.OpenAI`` client, pointed at the competition LLM with
    your team code as the API key.

    ``url`` is the API's address, ``/v1`` included, used as given. It
    defaults to ``DEFAULT_COMPETITOR_LLM_URL``. ``team_code`` is your team's
    16-character code from the accounts portal. Everything ``openai.OpenAI``
    does works here; pass ``model=MODEL`` to its calls, or use ``ask``.

    429s are retried ``max_retries`` times, honouring ``Retry-After``. Errors
    are openai's own exceptions (``openai.RateLimitError`` and so on).
    """

    # openai's copy() and with_options() rebuild the client by calling the
    # class with api_key= and base_url=, so those keywords must keep working.
    def __init__(self, url: Optional[str] = None, team_code: Optional[str] = None, *,
                 max_retries: int = 3, **kwargs: Any):
        api_key = kwargs.pop("api_key", None)
        team_code = team_code or api_key
        base_url = kwargs.pop("base_url", None) or url or DEFAULT_COMPETITOR_LLM_URL
        # Without this check openai falls back to OPENAI_API_KEY.
        if not team_code:
            raise ValueError("team_code is required: your team's code from the accounts portal")
        # The competition endpoint sits behind a CDN that brotli-compresses
        # replies when the client offers "br", which openai's HTTP layer does
        # whenever the brotli package is installed — and with brotli older
        # than 1.2 its decoder then fails with "process() takes no keyword
        # arguments". Offering only the encodings the standard library decodes
        # keeps the client out of that, whatever else a team has installed.
        headers = dict(kwargs.pop("default_headers", None) or {})
        headers.setdefault("Accept-Encoding", "gzip, deflate")
        super().__init__(api_key=team_code, base_url=base_url, max_retries=max_retries,
                         default_headers=headers, **kwargs)

    def ask(self, prompt: str, *, system: Optional[str] = None,
            max_tokens: Optional[int] = None, **kwargs: Any) -> str:
        """Send one user message, with an optional system message, and return
        the reply's text. Extra keywords go to ``chat.completions.create``."""
        messages = []
        if system is not None:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        reply = self.chat.completions.create(model=MODEL, messages=messages, **kwargs)
        return reply.choices[0].message.content or ""


__all__ = ["DEFAULT_COMPETITOR_LLM_URL", "MODEL", "CompetitorLLM"]
