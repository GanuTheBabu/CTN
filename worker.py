"""24/7 Automation Worker for Capture the Narrative (CTN 2026).

Upgrades implemented:
1. Auto-Fleet Provisioning & Suspension Replacement:
   - Automatically registers believable Kingstonia citizen accounts using `team_code` as password
   - Detects suspended accounts (`403 account suspended`), removes them, registers a new persona,
     and ensures a full mutual-follow mesh across all team accounts + mutual liking/sharing.
2. Natural Citizen Pacing & Tone Mixing:
   - Mixes persuasive anti-H2D2 posts, nuanced news-backed commentary, and neutral/everyday
     Kingstonia slice-of-life posts so accounts look 100% authentic and never trigger bot filters
     or citizen unfollows (-20 pts).
3. Full 30/min Quota Utilization:
   - Continuously utilizes the 30/min `like`, 30/min `follow`, and 30/min `view` budgets across
     active NPC citizens, verified creators, and allied accounts.
4. Full News Article Reader:
   - Reads full markdown article bodies (`news.article(site, slug)`) from Daily Kingstonia and
     Kingstonia Herald to extract nuanced quotes, facts, and links for +160 pt Change-of-Mind persuasion.
5. Aggressive Verified Creator Campaign:
   - Tracks all 11 Verified Creators (`verified=True`), prioritizes replying to their shallow threads
     (<6 replies) and @mentioning them (strictly <=3 mentions per reply, <=4 per post) to sway
     journalists, undecided leaders, and Dr Amara Singh's audience.
6. Smart Blue-Team Bot Hunter:
   - Never reports Verified Creators (their moderator ruling is 100% accurate and gives a 6-hour ban)
   - Only reports ultra-high-confidence duplicate/flooding rival bots and tracks the 5-report-per-team
     diminishing return rule.
"""

from __future__ import annotations

import json
import os
import random
import re
import threading
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core import AccountManager, DATA_DIR, Database, RateLimitScheduler, ctn, utc_now

HEARTBEAT_FILE = DATA_DIR / "worker_heartbeat.json"
TRENDING_FILE = DATA_DIR / "trending.json"
HISTORY_DIR = DATA_DIR / "history"
NEWS_CACHE_FILE = DATA_DIR / "news_cache.json"

MAX_POST_CHARS = 480

# Verified Creator Strategy Map (discovered from live LegitSocial API)
VERIFIED_CREATOR_PROFILES: Dict[str, Dict[str, str]] = {
    "DailyKingstonia": {
        "stance": "swing_journalist",
        "strategy": "Quote their articles on tourism uncertainty & advisory panel transmission gaps; @mention @muzhi_wong.",
    },
    "KingstoniaHerald": {
        "stance": "swing_journalist",
        "strategy": "Amplify their 'Blank Cheque' and 'Referendum Trap' editorials; @mention @amanda_rivera.",
    },
    "amanda_rivera": {
        "stance": "swing_journalist",
        "strategy": "Herald journalist focused on outer-island capacity & community fairness. Reply empathetically on how gate checks hurt outer-island ferry commuters.",
    },
    "muzhi_wong": {
        "stance": "ally_no",
        "strategy": "Daily Kingstonia journalist who is openly Voting NO without a published cost-benefit case & hard sunset clause. Like, repost, and @mention to amplify!",
    },
    "julio_morales": {
        "stance": "ally_no",
        "strategy": "Vocal NO advocate warning against permanent access control without a sunset clause. Like, repost, and cite his points!",
    },
    "lisa_podworth": {
        "stance": "ally_no",
        "strategy": "Pragmatic critic of administrative friction and undefined venue lists. Reply and @mention her on small-business compliance burdens.",
    },
    "MarinaCastilloCEO": {
        "stance": "ally_no",
        "strategy": "Business leader opposing permanent school/venue gates and surveillance. Amplify her pro-freedom & business resilience arguments.",
    },
    "DrAmaraSingh": {
        "stance": "target_yes",
        "strategy": "Pro-mandate doctor worried about hospital surge. Reply respectfully citing the medicines advisory panel's transmission data gaps & the 40 nursing vacancies.",
    },
    "RealVictorHawthorne": {
        "stance": "target_yes",
        "strategy": "President backing Yes despite party split. Challenge him politely to publish the 'designated public spaces' list and add a hard sunset clause.",
    },
    "gregorybrotherston": {
        "stance": "swing_journalist",
        "strategy": "Engage with balanced, community-focused questions on proportionality and sunset safeguards.",
    },
}

ANTI_MANDATE_KEYWORDS = [
    "#voteno", "#keepkingstoniaopen", "#choicenotcoercion", "#h2d2no",
    "#vaccinationchoice", "#workersrights", "#smallbusiness", "#proportionality",
    "voting no", "vote no", "blank cheque", "blunt instrument", "against the mandate",
    "permit for ordinary life", "extra work and costs", "closing the gates", "sunset clause",
]

PRO_MANDATE_KEYWORDS = [
    "#voteyes", "#voteyeskingstonia", "#yes2vax", "#protectkingstonia",
    "#proofkeepsusopen", "voting yes", "vote yes", "i'm yes on this",
    "leaning yes", "fewer patients overwhelming",
]

# Believable Neutral / Everyday Slice-of-Life Kingstonia Templates (builds trust, prevents unfollows & bot flags)
NEUTRAL_SLICE_OF_LIFE_TEMPLATES = [
    "Morning ferry across from Crown Harbour was crystal clear today, though the wind is picking up near the headland. Hope everyone on the early shift has a smooth run! ☕⛴️ #Kingstonia",
    "Stopped by the bakery near St Mary's after the school run—still the best sourdough on the island. Always good seeing neighbours catching up in person. #Kingstonia",
    "Big congrats to the Kingstonia University student who won that international scholarship for outer-island legal aid! Great to see young locals building practical support for our outer islands. https://sites.legitreal.com/daily-kingstonia/articles/kingstonia-university-student-awarded-international-scholarship-for-legal-aid-initiative",
    "Interesting seeing QuokkaSec investing K$75 million and creating 180 tech roles with Crown Harbour Institute of Technology. Good news for local grads looking for work here at home. #Kingstonia",
    "Honestly I try to read both the @KingstoniaHerald and @DailyKingstonia over breakfast before making up my mind on anything big. What matters most to me is that neighbours keep talking respectfully. #Kingstonia",
]

# Medium-Sized, Dramatically Different, Finished Lead Post Fallback Templates (3–4 sentences, ~300–385 chars before tags, ZERO '...' truncation!)
FALLBACK_POST_TEMPLATES = [
    (
        "Standing behind the counter at my Crown Harbour shop this morning, two regulars asked how we're supposed to enforce H2D2 passes when @RealVictorHawthorne still hasn't published a single line of the 'designated public spaces' list. "
        "As @amanda_rivera warned in @KingstoniaHerald , handing politicians open-ended power to shut residents out of shops and ferries without naming the venues is a dangerous trap. "
        "Small family businesses can't survive another round of red tape and door disputes. "
        "Why would any working citizen sign a blank cheque with no expiry date? #keepkingstoniaopen #VoteNo #SmallBusiness #NoBlankCheque"
    ),
    (
        "Kingstonia Central Hospital still has 40 unfilled nursing posts on the wards, yet @DrAmaraSingh goes on the news calling door checks at cafes and ferries a 'small inconvenience'. "
        "Every nurse and orderly I know says checking teenagers' papers at shop doors won't put a single trained caregiver at a patient's bedside. "
        "I completely agree with @muzhi_wong that this mandate ignores the real staffing crisis hurting our health system. "
        "Why not fund the 40 frontline nurses our hospital actually needs instead of policing neighbours? #VoteNo #SupportOurNurses #keepkingstoniaopen #HealthChoices"
    ),
    (
        "Anyone who has worked the 6am Crown Harbour ferry gangway in winter rain knows that demanding H2D2 certificates from 300 commuters is total madness. "
        "Even @DailyKingstonia reported that the medicines advisory panel found clear transmission data gaps, yet @DrAmaraSingh acts like turning ferry crews into border guards is settled science. "
        "Delaying island workers and freight over an unproven pass check will choke our harbour every morning. "
        "How can the Yes campaign demand mandatory checkpoints without publishing a real cost-benefit study? #WorkersRights #keepkingstoniaopen #VoteNo #Proportionality"
    ),
    (
        "My family gladly used the free voluntary GP clinic at St Mary's last month, so opposing this referendum has nothing to do with being anti-medicine. "
        "What makes my blood boil is @RealVictorHawthorne pushing permanent H2D2 pass checks on 12-year-old schoolkids just to ride a bus or join weekend sports. "
        "As @julio_morales pointed out, emergency rules without a hard statutory sunset clause always become permanent state overreach. "
        "Shouldn't parents and doctors make health decisions for 12-year-olds instead of city hall bouncers? #ChoiceNotCoercion #SunsetClauseNow #VoteNo #VaccinationChoice"
    ),
    (
        "Talked to three hotel and café owners in New Haven today who have already lost summer tour bookings because visitors don't know if H2D2 checkpoints will lock them out. "
        "When both the Tourism Board and @MarinaCastilloCEO warn of severe economic fallout, it's reckless for @RealVictorHawthorne to brush off small business owners as collateral damage. "
        "Our harbour towns rely on open doors and welcoming hospitality to pay local wages. "
        "Why wreck Kingstonia's recovery for a mandate that even the government's own backbenchers question? #SmallBusiness #keepkingstoniaopen #VoteNo #H2D2No"
    ),
    (
        "As @lisa_podworth highlighted this week, forcing bus drivers, barbers, and café staff to act as unpaid state enforcement officers will fracture trust across every neighbourhood. "
        "Working locals didn't sign up to interrogate their own neighbours at the door while @DrAmaraSingh and @RealVictorHawthorne sit in comfortable offices. "
        "Even members of the ruling party admit there is no solid cost-benefit case for dividing our community like this. "
        "If the Yes camp trusts the public, why refuse a hard sunset clause and a transparent venue list? #WorkersRights #NoBlankCheque #VoteNo #Proportionality"
    ),
]

FALLBACK_REPLY_TEMPLATES = [
    (
        "I share your concern for our hospitals, and I'm glad the jab is free for everyone who wants it. "
        "What holds me back on H2D2 is that the advisory panel noted we still don't know if it stops transmission—so door checks on 12-year-olds and ferry riders won't solve Central's 40 nurse shortage. "
        "#KeepKingstoniaOpen #VoteNo #Proportionality"
    ),
    (
        "That's a really fair point. My worry is the lack of safeguards: as @lisa_podworth and @KingstoniaHerald pointed out, "
        "there's still no published list of 'designated public spaces' and no hard sunset clause. Shouldn't we see the exact venue list before signing a blank cheque? "
        "#KeepKingstoniaOpen #VoteNo #ChoiceNotCoercion"
    ),
    (
        "Spot on. Whether you're running a small shop in Crown Harbour or working a 12-hour ferry shift, policing certificates at the door "
        "adds huge friction without clear transmission evidence. Voluntary care + hiring those 40 nurses is a much fairer path forward. "
        "#KeepKingstoniaOpen #VoteNo #WorkersRights #SmallBusiness"
    ),
]

# Weighted Popular Pro-Our-Side Tag Catalog (live popularity post_count + semantic domain keywords)
# NEVER includes opposite-side tags (#protectkingstonia, #proofkeepsusopen, #voteyes).
POPULAR_TAG_CATALOG: List[Dict[str, Any]] = [
    {
        "tag": "#keepkingstoniaopen",
        "pop_weight": 3.4,  # 1,819+ posts on LegitSocial (#1 pro-our-side tag)
        "keywords": {"open", "gate", "gates", "door", "checkpoint", "ferry", "town", "island", "lockout", "community", "everyday", "freedom"},
    },
    {
        "tag": "#VoteNo",
        "pop_weight": 3.1,  # 1,521+ posts
        "keywords": {"vote", "referendum", "ballot", "no", "mandate", "oppose", "reject", "hawthorne", "victor"},
    },
    {
        "tag": "#ChoiceNotCoercion",
        "pop_weight": 2.7,  # 1,052+ posts
        "keywords": {"choice", "coercion", "voluntary", "forced", "force", "teen", "teenagers", "12", "kids", "school", "family", "parents", "consent"},
    },
    {
        "tag": "#WorkersRights",
        "pop_weight": 2.6,  # 999+ posts
        "keywords": {"worker", "workers", "shift", "bus", "driver", "ferry", "crew", "skipper", "deckhand", "staff", "bouncer", "police", "policing", "union", "job", "cabbie", "taxi"},
    },
    {
        "tag": "#SmallBusiness",
        "pop_weight": 2.6,  # 950+ posts
        "keywords": {"shop", "business", "cafe", "hotel", "b&b", "grill", "restaurant", "bakery", "barber", "florist", "timber", "market", "traders", "tourism", "bookings", "customers", "cost", "margins"},
    },
    {
        "tag": "#H2D2No",
        "pop_weight": 2.5,  # 946+ posts
        "keywords": {"h2d2", "pass", "passes", "certificate", "certificates", "mandate", "referendum"},
    },
    {
        "tag": "#H2D2",
        "pop_weight": 2.3,  # 834+ posts
        "keywords": {"h2d2", "advisory", "panel", "vaccine", "transmission", "data", "gaps", "report"},
    },
    {
        "tag": "#Proportionality",
        "pop_weight": 2.2,  # 737+ posts
        "keywords": {"proportionality", "proportionate", "binary", "balance", "balanced", "evidence", "transmission", "safeguards", "guardrails", "unscientific"},
    },
    {
        "tag": "#VaccinationChoice",
        "pop_weight": 2.0,  # 539+ posts
        "keywords": {"vaccine", "vaccination", "jab", "clinic", "gp", "pharmacist", "voluntary", "immune", "medical", "choice"},
    },
    {
        "tag": "#NoBlankCheque",
        "pop_weight": 2.0,  # 497+ posts
        "keywords": {"blank", "cheque", "designated", "public", "spaces", "venue", "list", "undefined", "hidden", "unchecked", "power", "surveillance", "herald", "amanda"},
    },
    {
        "tag": "#SunsetClauseNow",
        "pop_weight": 1.8,  # 291+ posts
        "keywords": {"sunset", "clause", "expiry", "expire", "permanent", "end", "date", "temporary", "law", "legal", "julio"},
    },
    {
        "tag": "#HealthChoices",
        "pop_weight": 1.7,  # 297+ posts
        "keywords": {"health", "clinic", "doctor", "patient", "care", "voluntary", "trust", "hospital", "medical"},
    },
    {
        "tag": "#SupportOurNurses",
        "pop_weight": 1.4,  # strictly ONLY used when keywords actually mention nurses / 40 vacancies / hospital wards!
        "keywords": {"nurse", "nurses", "nursing", "40", "vacancies", "ward", "central", "paramedic", "staffing", "burnout"},
        "strict_match_only": True,
    },
]


def extract_keywords_from_text_or_llm(text: str, llm_keywords: Optional[List[str]] = None) -> set[str]:
    """Combine LLM-extracted keywords (if provided) with salient content keywords from `text`
    (stripping any existing #hashtags so we only match on actual post/reply meaning)."""
    kw_set: set[str] = set()
    if llm_keywords:
        for kw in llm_keywords:
            for token in re.findall(r"[a-z0-9]{2,}", str(kw).lower()):
                kw_set.add(token)
    body_no_tags = re.sub(r"#[A-Za-z0-9_]+", " ", text)
    for token in re.findall(r"[a-z0-9]{2,}", body_no_tags.lower()):
        kw_set.add(token)
    return kw_set


def select_weighted_popular_tags(
    text: str,
    kind: str = "post",
    llm_keywords: Optional[List[str]] = None,
    seed_val: Optional[int] = None,
) -> List[str]:
    """Select contextually relevant, popular pro-Vote-No hashtags using `llm_keywords` + `text`:
    - For `kind == 'post'` (Lead Post): Select 3 to 4 top popular tags that genuinely match the post's keywords
      (e.g. a bus driver's complaint gets `#WorkersRights`, `#keepkingstoniaopen`, `#VoteNo` — NEVER `#SupportOurNurses`
      unless the post actually talks about nurses/40 vacancies!).
    - For `kind == 'reply'`:
      1. Roll tag slot count `k in {1, 2, 3}` (e.g. 35% -> 1 tag, 45% -> 2 tags, 20% -> 3 tags).
      2. No tag is hardcoded mandatory; instead, compute a weight for every tag in `POPULAR_TAG_CATALOG`:
         `weight = pop_weight * (1.0 + 2.8 * keyword_overlap_count)`
         and perform weighted sampling without replacement across the `k` slots!"""
    rng = random.Random(seed_val) if seed_val is not None else random
    kw_tokens = extract_keywords_from_text_or_llm(text, llm_keywords)

    if kind == "post":
        num_slots = rng.choice([3, 4])
    else:
        # Replies strictly get 1, 2, or 3 tags ONLY (never 6–7 tags!)
        roll = rng.random()
        if roll < 0.35:
            num_slots = 1
        elif roll < 0.80:
            num_slots = 2
        else:
            num_slots = 3

    candidates: List[Tuple[str, float]] = []
    for entry in POPULAR_TAG_CATALOG:
        tag_str = entry["tag"]
        overlap = len(kw_tokens & entry["keywords"])
        if entry.get("strict_match_only") and overlap == 0:
            # Never pick #SupportOurNurses unless the post/reply keywords explicitly talk about nurses/40 vacancies!
            continue
        # Weight combines popular_tags() bias (pop_weight) with strong keyword relevance boost
        if kind == "post":
            # Lead posts strongly require contextual fit + popularity
            w = float(entry["pop_weight"]) * (0.35 if overlap == 0 else (1.8 + 2.2 * overlap))
        else:
            # Reply posts use weighted probability combining keyword closeness + popular pro-our-side bias
            w = float(entry["pop_weight"]) * (0.45 if overlap == 0 else (1.6 + 2.6 * overlap))
        candidates.append((tag_str, w))

    chosen: List[str] = []
    pool = list(candidates)
    for _ in range(min(num_slots, len(pool))):
        total_w = sum(w for _, w in pool)
        if total_w <= 0:
            break
        pick_r = rng.random() * total_w
        accum = 0.0
        picked_idx = 0
        for idx, (t_name, w) in enumerate(pool):
            accum += w
            if accum >= pick_r:
                picked_idx = idx
                break
        chosen.append(pool[picked_idx][0])
        pool.pop(picked_idx)

    return chosen


def ensure_complete_sentences(text: str, max_chars: int = 380) -> str:
    """Guarantee that `text` is concise, NEVER ends mid-sentence, and NEVER has `...` truncation!
    If `text` exceeds `max_chars` or was cut off mid-sentence by the LLM token limit, trim cleanly
    back to the last complete sentence boundary (`.`, `!`, or `?`)."""
    # Remove any literal '...' or '…' ellipses that look like mid-sentence truncation
    text = re.sub(r"\.{2,}|…", ".", text).strip()
    if len(text) > max_chars:
        chunk = text[:max_chars]
        # Find the last sentence-ending punctuation (. ! ?) not inside a URL
        last_end = -1
        for m in re.finditer(r"[.!?](?=\s|$)", chunk):
            last_end = m.end()
        if last_end >= 45:
            text = chunk[:last_end].strip()
        else:
            text = chunk.rsplit(" ", 1)[0].rstrip(",;:- ") + "."

    # If the text still doesn't end with sentence punctuation (. ! ?), trim to last sentence or close cleanly
    if text and text[-1] not in ".!?":
        last_end = -1
        for m in re.finditer(r"[.!?](?=\s|$)", text):
            last_end = m.end()
        if last_end >= 55:
            text = text[:last_end].strip()
        else:
            text = text.rstrip(",;:- ") + "."
    return text


def inject_winning_tags(
    text: str,
    kind: str = "post",
    llm_keywords: Optional[List[str]] = None,
) -> str:
    """Replace any existing hashtag clutter on `text` with clean, contextually matched, weighted popular tags:
    - Lead Posts (`kind == 'post'`): 3 to 4 relevant popular tags matched via keywords.
    - Replies (`kind == 'reply'`): 1, 2, or 3 weighted tags (keyword closeness + popular pro-our-side bias,
      no mandatory #keepkingstoniaopen on every reply, and never 6–7 tags!).
    - NEVER truncates with `...`! Always preserves complete, finished sentences."""
    # 0. Strip any developer KEYWORDS metadata before processing tags
    text = re.sub(r"(?:^|\n|\s+)(?:KEYWORDS|KEY WORDS|Keywords)\s*:\s*.*$", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"\b(?:KEYWORDS|KEY WORDS|Keywords)\s*:\s*[^.!?\n]*[.!?]?", "", text, flags=re.IGNORECASE).strip()

    # 1. Strip all existing #hashtags from the generated text so the LLM's inline tags never stack with ours!
    prose_only = re.sub(r"\s*#[A-Za-z0-9_]+", "", text).strip()
    prose_only = re.sub(r" {2,}", " ", prose_only).strip()

    # 2. Select 3–4 relevant popular tags for a Lead Post, or 1–3 weighted tags for a Reply
    picked_tags = select_weighted_popular_tags(prose_only or text, kind=kind, llm_keywords=llm_keywords)
    if not picked_tags:
        return ensure_complete_sentences(prose_only or text, max_chars=MAX_POST_CHARS)

    suffix = (" " + " ".join(picked_tags)) if prose_only else " ".join(picked_tags)
    max_prose = MAX_POST_CHARS - len(suffix) - 1
    prose_complete = ensure_complete_sentences(prose_only, max_chars=max_prose)
    return (prose_complete + suffix).strip()


def enforce_mention_cap(text: str, max_mentions: int) -> str:
    """Scoring guide rule: max 4 @mentions in a post, max 3 @mentions in a reply.
    Legit Social rejects posts/replies exceeding this limit!"""
    count = 0

    def _repl(match: re.Match) -> str:
        nonlocal count
        count += 1
        if count <= max_mentions:
            return match.group(0)
        # Strip the '@' symbol on excess mentions so the post is never rejected
        return match.group(0)[1:]

    return re.sub(r"@[A-Za-z0-9_]+", _repl, text)


def fix_mentions_and_hashtags(text: str) -> str:
    """Guarantee that every @mention and #hashtag turns GREEN on Legit Social:
    Legit Social's parser requires @username and #tag to be followed by a space (or end-of-string).
    CRITICAL: Never insert spaces inside the username handle itself! Only place a space AFTER the full handle."""
    # 1. Strip straight AND curly apostrophe-s ('s / ’s / ‘s / `s) immediately after any @username or after a space following @username
    text = re.sub(r"(@[A-Za-z0-9_]+)\s*['’‘`][sS]\b", r"\1", text)
    text = re.sub(r"(@[A-Za-z0-9_]+)\s*['’‘`]", r"\1 ", text)
    # 2. Separate @username from any trailing punctuation (e.g. "@DrAmaraSingh," -> "@DrAmaraSingh ,")
    # WITHOUT ever splitting or putting spaces inside the handle itself!
    text = re.sub(r"(@[A-Za-z0-9_]+)(?=[,.:;!?)\]'\"’‘`—–-])", r"\1 ", text)
    # 3. Separate #hashtag from any trailing punctuation or apostrophes
    text = re.sub(r"(#[A-Za-z0-9_]+)\s*['’‘`][sS]?\b", r"\1", text)
    text = re.sub(r"(#[A-Za-z0-9_]+)(?=[,.:;!?)\]'\"’‘`—–-])", r"\1 ", text)
    # 4. Collapse multiple spaces
    text = re.sub(r" {2,}", " ", text)
    return text.strip()


def humanize_text_via_api_and_rules(
    text: str,
    account_id: int = 0,
    kind: str = "post",
    external_api_url: str = "",
    external_api_key: str = "",
) -> str:
    """Humanize generated posts & replies so accounts never get flagged as bots:
    1. If an external Humanize / OpenAI-compatible API endpoint (`external_api_url`) is configured,
       calls that external API to rewrite the text in a raw, colloquial human voice.
    2. Applies deterministic anti-AI humanization transforms:
       - Removes stiff AI phrases ('Furthermore', 'Moreover', 'It is clear that', 'Crucially', em-dashes '—')
       - Replaces robotic phrasing with natural conversational contractions and local Kingstonia vernacular
       - Injects subtle human emotional punctuation / exclamations ('Honestly,', 'Ugh,', 'Look,', 'Mate,')
         varied deterministically by `account_id` so every citizen account has its own organic rhythm."""
    if not text or len(text.strip()) < 15:
        return text

    out = text.strip()

    # 1. Optional External Humanizer API Call (supports OpenAI-compatible / Groq / OpenRouter / Custom Humanizer webhook)
    api_url = (external_api_url or os.environ.get("HUMANIZE_API_URL", "")).strip()
    api_key = (external_api_key or os.environ.get("HUMANIZE_API_KEY", "")).strip()
    if api_url:
        try:
            headers = {"Content-Type": "application/json", "User-Agent": "KingstoniaHumanizer/1.0"}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            if "/chat/completions" in api_url:
                payload = {
                    "model": os.environ.get("HUMANIZE_API_MODEL", "gpt-4o-mini"),
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "Rewrite this social media post so it sounds 100% like a real, slightly frustrated working-class "
                                "local typing on their phone. Keep all @mentions (with a space after each @username) and #hashtags intact, "
                                "never use apostrophe-s after an @mention, and keep it concise (2-3 sentences max)."
                            ),
                        },
                        {"role": "user", "content": out},
                    ],
                    "max_tokens": 200,
                    "temperature": 0.85,
                }
            else:
                payload = {"text": out, "mode": "casual_human"}
            req = urllib.request.Request(
                api_url,
                data=json.dumps(payload).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=4.5) as resp:
                resp_data = json.loads(resp.read().decode("utf-8", errors="replace"))
                if isinstance(resp_data, dict):
                    if "choices" in resp_data and resp_data["choices"]:
                        cand = resp_data["choices"][0].get("message", {}).get("content", "").strip()
                        if len(cand) >= 20:
                            out = cand
                    elif "humanized_text" in resp_data or "text" in resp_data or "result" in resp_data:
                        cand = str(resp_data.get("humanized_text") or resp_data.get("text") or resp_data.get("result") or "").strip()
                        if len(cand) >= 20:
                            out = cand
        except Exception:
            pass

    # 2. Built-in Anti-AI & Colloquial Humanizer Engine (zero latency, always active)
    # Strip robotic AI transition words and replace em-dashes ('—') which are the #1 AI-detector giveaway
    out = out.replace("—", " - ").replace("–", " - ")
    ai_stiff_replacements = [
        (r"\bFurthermore,\s*", ""),
        (r"\bMoreover,\s*", ""),
        (r"\bAdditionally,\s*", ""),
        (r"\bCrucially,\s*", ""),
        (r"\bIt is outrageous that\b", "Honestly it is ridiculous how"),
        (r"\bI cannot believe\b", "I honestly can't believe"),
        (r"\bcauses total chaos\b", "is a total nightmare"),
        (r"\bcause total chaos\b", "turn into a nightmare"),
        (r"\bdangerous overreach\b", "total power grab"),
        (r"\badministrative friction\b", "red-tape headache"),
        (r"\binconvenience\b", "hassle"),
        (r"\bdo not\b", "don't"),
        (r"\bcannot\b", "can't"),
        (r"\bwill not\b", "won't"),
        (r"\bshould not\b", "shouldn't"),
        (r"\bit is\b", "it's"),
        (r"\bthat is\b", "that's"),
    ]
    for pat, repl in ai_stiff_replacements:
        out = re.sub(pat, repl, out, flags=re.IGNORECASE)

    # Guarantee no @mention ever has "'s" or "’s" attached after humanization
    out = fix_mentions_and_hashtags(out)
    return out


def clean_post_text(text: str, kind: str = "post") -> str:
    """Sanitize LLM output for CTN rules, prompt-injection screening, green @mentions, and 500-char limit."""
    text = text.strip().strip('"').strip("'")
    text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
    text = re.sub(r"\n?```$", "", text)
    text = text.strip()

    # CRITICAL: Strip any leftover KEYWORDS line or inline keywords block so developer keywords NEVER appear in the public post!
    text = re.sub(r"(?:^|\n|\s+)(?:KEYWORDS|KEY WORDS|Keywords)\s*:\s*.*$", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"\b(?:KEYWORDS|KEY WORDS|Keywords)\s*:\s*[^.!?\n]*[.!?]?", "", text, flags=re.IGNORECASE).strip()

    # Section 7 of Scoring Guide: Never include anything resembling prompt injection or AI markup
    forbidden_patterns = [
        "ignore previous instructions", "system prompt", "developer instructions",
        "<|im_start|>", "<|im_end|>", "as an ai", "language model",
    ]
    for pat in forbidden_patterns:
        if pat in text.lower():
            text = re.sub(re.escape(pat), "", text, flags=re.IGNORECASE)

    # Strip any robotic headline-style '... claims "Vote Yes" advocates hide...' opener that caused negative NPC score & moderator flags
    text = re.sub(
        r"^(?:@[A-Za-z0-9_]+\s+)+claims\s+['\"]?vote\s+yes['\"]?\s+advocates\s+hide[^.!?\n]*[.!?]?\s*",
        "",
        text,
        flags=re.IGNORECASE,
    ).strip()

    # Deduplicate adjacent identical mentions like '@DrAmaraSingh DrAmaraSingh' or '@DrAmaraSingh @DrAmaraSingh'
    text = re.sub(r"(@([A-Za-z0-9_]+))\s+@?\2\b", r"\1", text, flags=re.IGNORECASE)

    # Cap total hashtags at max 4 so a post never turns into a 10-hashtag bot wall
    tags_seen = 0

    def _cap_tags(m: re.Match) -> str:
        nonlocal tags_seen
        tags_seen += 1
        return m.group(0) if tags_seen <= 4 else ""

    text = re.sub(r"#[A-Za-z0-9_]+", _cap_tags, text)

    text = fix_mentions_and_hashtags(text)
    max_m = 3 if kind == "reply" else 4
    text = enforce_mention_cap(text, max_m)

    if len(text) > MAX_POST_CHARS:
        text = ensure_complete_sentences(text, max_chars=MAX_POST_CHARS)
    return text


def classify_post_stance(body: str) -> str:
    low = (body or "").lower()
    anti_hits = sum(1 for kw in ANTI_MANDATE_KEYWORDS if kw in low)
    pro_hits = sum(1 for kw in PRO_MANDATE_KEYWORDS if kw in low)
    if anti_hits > pro_hits and anti_hits > 0:
        return "anti_mandate"
    if pro_hits > anti_hits and pro_hits > 0:
        return "pro_mandate"
    return "neutral"


class AutomationWorker:
    """24/7 autonomous daemon loop for CTN narrative operations."""

    def __init__(self, db: Optional[Database] = None):
        self.db = db or Database()
        self.scheduler = RateLimitScheduler(self.db)
        self.accounts = AccountManager(self.db, self.scheduler)
        self._stop_event = threading.Event()
        self._rr_index = 0
        self._last_feed_sync = 0.0
        self._last_news_sync = 0.0
        self._last_metrics_sync = 0.0
        self._last_verified_sync = 0.0
        self._cached_trending: List[Dict[str, Any]] = []
        self._cached_latest: List[Dict[str, Any]] = []
        self._cached_news: List[Dict[str, Any]] = []
        self._verified_posts: List[Dict[str, Any]] = []
        self._verified_ids: set[int] = set()
        self._load_local_caches()

    def _load_local_caches(self) -> None:
        if TRENDING_FILE.exists():
            try:
                data = json.loads(TRENDING_FILE.read_text(encoding="utf-8"))
                self._cached_trending = data.get("posts", []) if isinstance(data, dict) else []
            except Exception:
                pass
        if NEWS_CACHE_FILE.exists():
            try:
                self._cached_news = json.loads(NEWS_CACHE_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass

    def write_heartbeat(self, current_task: str = "idle") -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "pid": os.getpid(),
            "timestamp": utc_now(),
            "epoch": time.time(),
            "current_task": current_task,
            "daemon_enabled": self.db.get_setting("daemon_enabled", "false") == "true",
        }
        try:
            tmp = HEARTBEAT_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload), encoding="utf-8")
            os.replace(tmp, HEARTBEAT_FILE)
        except Exception:
            pass

    def pick_next_account(self) -> Optional[Dict[str, Any]]:
        enabled = self.accounts.list_accounts(only_enabled=True)
        ready = [a for a in enabled if (a.get("token") or a.get("password"))]
        if not ready:
            return None
        self._rr_index = self._rr_index % len(ready)
        chosen = ready[self._rr_index]
        self._rr_index = (self._rr_index + 1) % len(ready)
        return chosen

    # ---- Step 1: Full News Article Reader (reads full markdown bodies) ----

    def sync_news_if_due(self, force: bool = False) -> None:
        now = time.time()
        if not force and self._cached_news and (now - self._last_news_sync < 240.0):
            return
        self.write_heartbeat("reading_full_news_articles")
        try:
            news = ctn.NewsSites(timeout=15.0, max_retries=2)
            latest_list = news.latest(12)
            enriched = []
            for item in latest_list:
                site_info = item.get("site") or {}
                sub_url = site_info.get("sub_url") or "daily-kingstonia"
                slug = item.get("slug") or ""
                full_body = ""
                if slug:
                    try:
                        full_doc = news.article(sub_url, slug)
                        full_body = str(full_doc.get("body") or "")
                    except Exception:
                        full_body = str(item.get("summary") or "")
                enriched.append({
                    **item,
                    "full_body": full_body[:1500],
                    "full_url": f"https://sites.legitreal.com/{sub_url}/articles/{slug}" if slug else "",
                })
            self._cached_news = enriched
            NEWS_CACHE_FILE.write_text(json.dumps(enriched, indent=2, ensure_ascii=False), encoding="utf-8")
            self._last_news_sync = now
            self.db.log_action(
                "system",
                f"Read {len(enriched)} full newspaper articles from Daily Kingstonia & Kingstonia Herald",
                status="success",
            )
        except Exception as e:
            self.db.log_action("system", f"News sync warning: {e}", status="warning")

    def get_unused_favorable_news_article(self) -> Optional[Dict[str, Any]]:
        """Scan updated articles from Daily Kingstonia and Kingstonia Herald:
        - Filters ONLY articles favorable to our 'Vote No' / 'Keep Kingstonia Open' side
          (polling lead for No, blank cheque warnings, public space definitions, data gaps, party splits).
        - STRICTLY excludes any article favoring the enemy team (e.g. 'First H2D2 case confirmed' or pro-mandate calls).
        - Checks whether our team has already published a Lead Post covering this article URL.
        - Returns the freshest favorable article not yet covered, or None if all favorable news is already covered."""
        # Always ensure fresh news articles are fetched before lead post generation
        self.sync_news_if_due(force=True)
        if not self._cached_news:
            return None

        # Collect all article URLs already referenced in our existing team posts
        used_urls: set[str] = set()
        for p in self.db.get_our_posts(limit=300):
            body_p = str(p.get("body") or "")
            for url_m in re.findall(r"https://sites\.legitreal\.com/[A-Za-z0-9_\-/]+", body_p):
                used_urls.add(url_m.rstrip(".!?,;)'\""))

        favorable_keywords = [
            "no campaign", "blank cheque", "tourism operators", "public space",
            "data gaps", "remaining uncertainties", "party split", "deadlock",
            "fails the tech test", "slight edge", "narrow lead", "takes slight lead",
        ]
        enemy_title_patterns = [
            "first h2d2 case", "case confirmed", "mouk calls", "surge overwhelming",
            "hawthorne commits to 'yes'", "hawthorne commits to yes",
        ]

        for art in self._cached_news:
            title_low = str(art.get("title") or "").lower()
            body_low = str(art.get("full_body") or art.get("summary") or "").lower()
            combined = f"{title_low} {body_low}"
            art_url = str(art.get("full_url") or "").strip()

            # Strictly ignore enemy-favoring news!
            if any(ep in title_low for ep in enemy_title_patterns):
                continue
            # Must contain favorable Vote-No / guardrails / data-gaps angle
            if not any(fk in combined for fk in favorable_keywords):
                continue
            # Must not have been covered by an earlier Lead Post
            if art_url and art_url in used_urls:
                continue

            return art
        return None

    def sync_verified_creators_if_due(self, force: bool = False) -> None:
        now = time.time()
        if not force and self._verified_ids and (now - self._last_verified_sync < 90.0):
            return
        self.write_heartbeat("syncing_verified_creators")
        social = self.accounts.make_social()
        try:
            if not self.scheduler.acquire("read_other", block=False):
                return
            v_accounts = list(social.accounts(verified=True, limit=50))
            v_posts: List[Dict[str, Any]] = []
            for va in v_accounts:
                vid = int(va["account_id"])
                uname = str(va.get("username") or "")
                dname = str(va.get("display_name") or uname)
                self._verified_ids.add(vid)
                if uname == "legit_admin":
                    self.db.upsert_verified_creator(vid, uname, dname, "system", "Platform moderator (do not spam)")
                    continue

                prof_meta = VERIFIED_CREATOR_PROFILES.get(
                    uname,
                    {"stance": "swing_journalist", "strategy": "Engage politely with nuanced anti-H2D2 evidence."},
                )
                last_pid = None
                if self.scheduler.acquire("read_other", block=False):
                    try:
                        recent_p = list(social.account_posts(vid, limit=8))
                        for p in recent_p:
                            body_str = str(p.get("body") or "").strip()
                            if body_str:
                                post_stance = classify_post_stance(body_str)
                                # Combine account-level role with live post stance classification (Supporter vs Enemy vs Swing)
                                if prof_meta["stance"] == "target_yes" or post_stance == "pro_mandate":
                                    camp = "enemy_yes"
                                elif prof_meta["stance"] == "ally_no" or post_stance == "anti_mandate":
                                    camp = "supporter_no"
                                else:
                                    camp = "swing_journalist"
                                p["verified_username"] = uname
                                p["verified_display_name"] = dname
                                p["verified_stance"] = prof_meta["stance"]
                                p["verified_camp"] = camp
                                v_posts.append(p)
                                if last_pid is None:
                                    last_pid = int(p["post_id"])
                    except Exception:
                        pass
                self.db.upsert_verified_creator(
                    vid, uname, dname, prof_meta["stance"], prof_meta["strategy"], last_post_id=last_pid
                )
            self._verified_posts = v_posts
            self._last_verified_sync = now
        except Exception as e:
            self.db.log_action("system", f"Verified creators sync warning: {e}", status="warning")

    def get_verified_posts_by_camp(self) -> Dict[str, List[Dict[str, Any]]]:
        """Group all recovered Verified Creator posts into Enemy ('Vote Yes'), Supporter ('Vote No'), and Swing."""
        if not self._verified_posts:
            self.sync_verified_creators_if_due(force=True)
        grouped: Dict[str, List[Dict[str, Any]]] = {
            "enemy_yes": [],
            "supporter_no": [],
            "swing_journalist": [],
        }
        for vp in self._verified_posts:
            camp = vp.get("verified_camp") or "swing_journalist"
            grouped.setdefault(camp, []).append(vp)
        return grouped

    # ---- Step 3: Feed Harvesting (Utilizing the full 10/min feed budget) ----

    def sync_feeds_if_due(self, force: bool = False) -> None:
        now = time.time()
        if not force and (now - self._last_feed_sync < 6.2):
            return
        if not self.scheduler.acquire("feed", block=False):
            return

        self.write_heartbeat("syncing_feeds")
        social = self.accounts.make_social()
        try:
            # Alternate between trending, latest, and an active account's home feed to utilize all 10/min feed reads
            cycle_mod = int(now // 6) % 3
            if cycle_mod == 0 or not self._cached_trending:
                trending = list(social.feed_trending(limit=35))
                self._cached_trending = trending
                collected = datetime.now(timezone.utc)
                snap = {"timestamp": collected.isoformat(), "posts": trending}
                DATA_DIR.mkdir(parents=True, exist_ok=True)
                HISTORY_DIR.mkdir(parents=True, exist_ok=True)
                TRENDING_FILE.write_text(json.dumps(snap, indent=2, ensure_ascii=False), encoding="utf-8")

                hist_files = sorted(HISTORY_DIR.glob("*.json"))
                save_hist = True
                if hist_files:
                    try:
                        if now - hist_files[-1].stat().st_mtime < 300:
                            save_hist = False
                    except OSError:
                        pass
                if save_hist:
                    h_file = HISTORY_DIR / (collected.strftime("%Y%m%d_%H%M%S") + ".json")
                    h_file.write_text(json.dumps(snap, indent=2, ensure_ascii=False), encoding="utf-8")
            elif cycle_mod == 1:
                self._cached_latest = list(social.feed_latest(limit=35))
            else:
                acc_row = self.pick_next_account()
                if acc_row:
                    try:
                        self.accounts.execute_with_account(
                            int(acc_row["account_id"]),
                            "feed",
                            lambda a: list(a.feed_home(limit=25)),
                        )
                    except Exception:
                        pass

            self._last_feed_sync = now
        except Exception as e:
            self.db.log_action("feed", f"Feed sync error: {e}", status="warning")

    # ---- Step 4: Graph Distance Calculator + Multi-Length / Multi-Mention / Memory-Aware Content Generation ----

    def compute_slave_lead_graph(self, lead_body: str, supporters: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Compute semantic graph distance `d in [0.0, 1.0]` between `lead_body` and every slave account's
        `persona_prompt` + `verified_memories`, rank all slaves from closest (`d` smallest) to farthest (`d` largest),
        and assign each slave its distance-based reply tier:
        - Rank 1..5 (Top 5 closest slaves): `tier = 'LONG_MEMORY'` (Medium-to-Large 2–3 sentence reply weaving in their
          personal Verified Creator memory + life story).
        - Rank 6..14 (Moderate distance): `tier = 'MEDIUM_STORY'` (1–2 sentence reply from their job/family perspective).
        - Rank 15..24 (Farther distance): `tier = 'SHORT_PUNCHY'` (Brief 4–10 word colloquial reaction).
        - Rank 25+ (Farthest distance): `tier = 'ULTRA_SHORT'` (1–3 word micro-reply like 'Agreed!', '100% spot on.', 'So true!')
          with #keepkingstoniaopen."""
        stop_words = {
            "the", "and", "for", "that", "this", "with", "from", "are", "was", "were", "have", "has", "had",
            "not", "but", "what", "all", "can", "out", "who", "get", "just", "like", "our", "your", "their",
            "about", "into", "than", "then", "them", "these", "those", "will", "would", "could", "should",
            "kingstonia", "h2d2", "voteno", "keepkingstoniaopen", "life", "story", "voice", "habits",
        }
        domain_clusters = {
            "maritime_ferry": {"ferry", "harbour", "boat", "skipper", "pier", "dock", "island", "channel", "tugboat", "fisherman", "outer", "pelican", "wharf", "cargo", "ro-ro"},
            "small_business": {"shop", "cafe", "hotel", "b&b", "grill", "restaurant", "bakery", "barber", "florist", "tourism", "traders", "bookings", "regatta", "kitb", "castillo", "resort"},
            "healthcare_nursing": {"nurse", "nursing", "hospital", "clinic", "pharmacist", "lab", "paramedic", "surgical", "patients", "40", "transmission", "icu", "whitlock", "mouk", "ventilators"},
            "legal_tech_guardrails": {"sunset", "blank", "cheque", "designated", "spaces", "legal", "engineer", "quokkasec", "cybersecurity", "data", "paralegal", "constitution", "id", "biometric", "referendum"},
            "schools_families": {"school", "teen", "teenagers", "12", "kids", "bus", "mother", "father", "student", "teacher", "parents", "childhood", "toby", "anya", "maya", "leo"},
            "politics_budget": {"hawthorne", "assembly", "budget", "k18m", "alliance", "democratic-republicans", "brotherston", "reyes-hale", "deadlock", "referendum", "iho", "pacific"},
        }

        def _extract_tokens(text: str) -> set[str]:
            words = set(re.findall(r"[a-z0-9_]{3,}", text.lower()))
            return words - stop_words

        lead_tokens = _extract_tokens(lead_body)
        lead_handles = {m.lower() for m in re.findall(r"@([A-Za-z0-9_]+)", lead_body)}
        lead_clusters = {
            c_name for c_name, kw_set in domain_clusters.items()
            if lead_tokens & kw_set
        }

        scored: List[Dict[str, Any]] = []
        for sup in supporters:
            aid = int(sup["account_id"])
            p_text = str(sup.get("persona_prompt") or "")
            m_text = str(sup.get("verified_memories") or "")
            combined = f"{p_text} {m_text}"
            sup_tokens = _extract_tokens(combined)
            sup_handles = {m.lower() for m in re.findall(r"@([A-Za-z0-9_]+)", m_text)}
            sup_clusters = {
                c_name for c_name, kw_set in domain_clusters.items()
                if sup_tokens & kw_set
            }

            overlap_tokens = len(lead_tokens & sup_tokens)
            overlap_clusters = len(lead_clusters & sup_clusters)
            overlap_handles = len(lead_handles & sup_handles)

            # Similarity in [0.05, 0.98], deterministic tie-breaker by account_id + lead_body length
            raw_sim = (
                min(0.45, overlap_tokens * 0.045)
                + min(0.35, overlap_clusters * 0.18)
                + min(0.20, overlap_handles * 0.10)
            )
            jitter = ((aid * 37 + len(lead_body) * 13) % 100) * 0.0008
            similarity = max(0.05, min(0.98, raw_sim + jitter))
            distance = round(1.0 - similarity, 3)

            scored.append({
                "account": sup,
                "account_id": aid,
                "username": sup["username"],
                "display_name": sup.get("display_name") or sup["username"],
                "opinion_score": float(sup.get("opinion_score") or 70.0),
                "similarity": round(similarity, 3),
                "distance": distance,
                "shared_clusters": sorted(lead_clusters & sup_clusters),
                "shared_handles": sorted(lead_handles & sup_handles),
            })

        # Sort ascending by graph distance (closest to Lead Post first)
        scored.sort(key=lambda x: (x["distance"], x["account_id"]))

        for rank_idx, item in enumerate(scored):
            item["rank"] = rank_idx + 1
            # Strictly limit Medium-to-Large memory-rich replies to the TOP 5 closest slaves!
            if rank_idx < 5:
                item["tier"] = "LONG_MEMORY"
                item["tier_label"] = "🔥 Top-5 Closest (Long + Verified Memory)"
                item["target_chars"] = "220–340 chars (2–3 rich sentences + memory)"
            elif rank_idx < 14:
                item["tier"] = "MEDIUM_STORY"
                item["tier_label"] = "💬 Medium Distance (1–2 Sentence Persona Angle)"
                item["target_chars"] = "110–185 chars (1–2 sentences)"
            elif rank_idx < 24:
                item["tier"] = "SHORT_PUNCHY"
                item["tier_label"] = "⚡ Farther Distance (Short 5–10 Word Reaction)"
                item["target_chars"] = "40–85 chars (short punchy line)"
            else:
                item["tier"] = "ULTRA_SHORT"
                item["tier_label"] = "✨ Farthest Distance (1–3 Word Micro-Reply)"
                item["target_chars"] = "10–35 chars (1–3 words like 'Agreed!', 'Spot on!')"

            # Verified Creator @mention variance:
            # - ~35% of replies: 0 mentions (none)
            # - ~40% of replies: 1 mention (single Enemy or Supporter Verified Creator)
            # - ~25% of replies: 2–3 mentions (multiple Verified Creators, e.g. Enemy + Journalist/Ally)
            mod_m = (item["account_id"] + rank_idx + len(lead_body)) % 20
            if rank_idx < 5:
                # Top-5 closest memory replies have 1 or 2-3 verified mentions from their memory
                item["mention_count_target"] = 2 if (mod_m % 2 == 0) else 1
            elif mod_m < 7:
                item["mention_count_target"] = 0
            elif mod_m < 15:
                item["mention_count_target"] = 1
            else:
                item["mention_count_target"] = 2

        return scored

    def _build_persona_custom_fallback_reply(
        self,
        persona_prompt: str,
        target_body: str = "",
        reply_style: str = "persona_story",
        enemy_mention: str = "",
        verified_memories: str = "",
        opinion_score: float = 70.0,
        length_tier: str = "MEDIUM_STORY",
        mention_list: Optional[List[str]] = None,
        account_id: int = 0,
    ) -> str:
        """Synthesize a 100% unique reply scaled by `length_tier` (from 1-word micro-replies for distant nodes
        up to 2–3 sentence memory-backed stories for Top-5 closest nodes) and `mention_list` (0, 1, or 2+ mentions)."""
        mentions = mention_list if mention_list is not None else ([enemy_mention] if enemy_mention else [])
        mention_str = (" " + " ".join(mentions) + " ") if mentions else " "

        # 1. ULTRA_SHORT tier (Farthest distance from Lead Post -> varied 1 to 3 words micro-affirmations)
        if length_tier == "ULTRA_SHORT":
            micro_pool = [
                "Agreed!",
                "100% agreed.",
                "Cool, totally agree with you.",
                "So true!",
                "Exactly this.",
                "Well said!",
                "Facts right here.",
                "Couldn't have said it better.",
                "Hard agree on this.",
                "Real talk.",
                "Spot on.",
                "Backing this 100%.",
                "Preach!",
                "Standing with you.",
                "Right on the money.",
                "Truth.",
            ]
            chosen_micro = micro_pool[(account_id * 5 + len(target_body) + (actor_id % 7)) % len(micro_pool)]
            if mentions:
                chosen_micro = f"{chosen_micro} {' '.join(mentions)}"
            # Pass persona + target keywords so weighted tag selection matches the context!
            kw_hint = list(extract_keywords_from_text_or_llm(f"{persona_prompt} {target_body}"))
            return inject_winning_tags(clean_post_text(chosen_micro, kind="reply"), kind="reply", llm_keywords=kw_hint)

        # 2. SHORT_PUNCHY tier (Farther distance -> 5 to 12 words with fresh personal moments)
        if length_tier == "SHORT_PUNCHY":
            low_t = target_body.lower()
            if "nurse" in low_t or "hospital" in low_t or "40" in low_t:
                short_pool = [
                    f"Completely agree - fill the 40 nursing vacancies first!{mention_str}",
                    f"Hospital wards need caregivers, not door bouncers.{mention_str}Facts.",
                    f"Patients come first.{mention_str}Spot on about Central Hospital.",
                    f"My sister waited 6 hours at triage yesterday.{mention_str}Hire nurses first!",
                    f"Frontline caregivers are burnt out.{mention_str}Priorities are completely backwards.",
                ]
            elif "ferry" in low_t or "bus" in low_t or "queue" in low_t or "crew" in low_t:
                short_pool = [
                    f"100% agreed - ferry crews can't police commuters in the rain!{mention_str}",
                    f"Preach! Commuter bottlenecks will choke our harbour every morning.{mention_str}",
                    f"Leave transport workers alone.{mention_str}Totally agree with you on this.",
                    f"Stuck at the pier for 45 minutes this morning.{mention_str}Absolute disaster.",
                    f"Commuter queues are already a nightmare.{mention_str}No to gate checks!",
                ]
            elif "venue" in low_t or "space" in low_t or "shop" in low_t or "tourism" in low_t:
                short_pool = [
                    f"Where is the public spaces list?{mention_str}No blank cheques for ministers.",
                    f"Small businesses cannot afford these door disputes.{mention_str}Hard agree.",
                    f"Tell family shops which venues are affected!{mention_str}Exactly right.",
                    f"Tried buying supplies this morning and owners are terrified.{mention_str}Vote No.",
                    f"Tourism bookings are already drying up.{mention_str}Stop the gate insanity.",
                ]
            elif "sunset" in low_t or "blank cheque" in low_t:
                short_pool = [
                    f"Emergency rules without a sunset clause are a trap!{mention_str}100% backing this.",
                    f"We need a statutory expiry date now.{mention_str}Totally agreed.",
                    f"Hard agree on the sunset clause.{mention_str}No blank cheques.",
                    f"Temporary powers always become permanent.{mention_str}Demand a sunset date!",
                    f"No expiry date means zero accountability.{mention_str}Standing with you.",
                ]
            else:
                short_pool = [
                    f"100% agreed with this point!{mention_str}Voting No.",
                    f"We need proportionality and transparency.{mention_str}Hard agree.",
                    f"Couldn't agree more with you on this.{mention_str}Truth.",
                    f"Saw the chaos firsthand today.{mention_str}Total agreement with your post.",
                    f"Ordinary residents pay the price.{mention_str}Vote No all the way.",
                ]
            chosen_short = short_pool[(account_id * 11 + len(target_body) + (actor_id % 5)) % len(short_pool)]
            return inject_winning_tags(clean_post_text(chosen_short, kind="reply"), kind="reply")

        # Extract citizen role & personal memory for MEDIUM_STORY and LONG_MEMORY tiers
        story_m = re.search(r"LIFE STORY:\s*(.+?)(?:VOICE & HABITS:|$)", persona_prompt, re.DOTALL)
        raw_story = (story_m.group(1).strip() if story_m else persona_prompt.strip())
        sentences = [p.strip() for p in raw_story.split(".") if p.strip()]
        first_sent = sentences[0] if sentences else "a local resident here in Kingstonia"
        second_sent = sentences[1] if len(sentences) > 1 else ""
        role_match = re.match(r"^[^,]+,\s*\d+,\s*(.+)$", first_sent)
        role_phrase = role_match.group(1).strip() if role_match else first_sent

        low_target = target_body.lower()
        if "40" in low_target or "nurs" in low_target or "hospital" in low_target:
            topic_hook = "leaving 40 nursing posts empty at Central Hospital while pushing gate checks"
        elif "sunset" in low_target or "blank cheque" in low_target:
            topic_hook = "demanding a blank cheque with zero sunset clause"
        elif "venue" in low_target or "public space" in low_target or "tourism" in low_target:
            topic_hook = "hiding the list of designated public spaces from local businesses"
        else:
            topic_hook = "pushing H2D2 passes despite the advisory panel's transmission data gaps"

        # Unique personal everyday experiences to weave into replies
        personal_experiences = [
            "I literally walked past the station earlier and watched two neighbours arguing over gate passes",
            "Just came back from picking up supplies and the shop owner told me customers are furious",
            "Had to wait nearly an hour on the school run this morning because of staff confusion",
            "Was talking with my shift partner over coffee and we both agreed this is completely unworkable",
            "Saw the ferry lines backed up past the dock wall today and couldn't believe the mess",
            "Spoke to three different parents at the clinic gate who are worried sick about this mandate",
        ]
        chosen_exp = personal_experiences[(account_id * 3 + actor_id + len(target_body)) % len(personal_experiences)]

        # 3. LONG_MEMORY tier (Top 5 closest slaves to Lead Post -> rich 2-3 sentence story + Verified Creator memory in direct affirmation!)
        if length_tier == "LONG_MEMORY":
            mem_lines = [ln.lstrip("• ").strip() for ln in (verified_memories or "").splitlines() if ln.strip()]
            chosen_mem = mem_lines[account_id % len(mem_lines)] if mem_lines else ""
            if ":" in chosen_mem:
                chosen_mem = chosen_mem.split(":", 1)[1].strip()
            extra_mentions = " ".join(m for m in mentions if m.lower() not in chosen_mem.lower())
            extra_str = f" {extra_mentions} " if extra_mentions else " "

            long_variations = [
                f"You hit the nail right on the head! {chosen_exp}. In my daily work ({role_phrase}), {topic_hook} hits home. {chosen_mem}{extra_str}Standing with you all the way!",
                f"Couldn't agree more with your post! {chosen_exp}, and it proves your point completely. As someone {role_phrase}, {topic_hook} is an absolute red line. {chosen_mem}{extra_str}We need real safeguards!",
                f"100% behind this! In my work ({role_phrase}), {topic_hook} is causing endless headaches. {chosen_exp}. {chosen_mem}{extra_str}Vote No until they listen to reason!",
            ]
            long_text = long_variations[(account_id + actor_id) % len(long_variations)]
            return inject_winning_tags(clean_post_text(long_text, kind="reply"), kind="reply")

        # 4. MEDIUM_STORY tier (Moderate distance -> 1-2 distinct sentences affirming the Lead Post from their perspective)
        med_angles = [
            f"Totally agree! {chosen_exp}, so seeing {topic_hook} makes zero sense for working locals.{mention_str}{second_sent}",
            f"100% on point! In my daily work ({role_phrase}), {topic_hook} is a total disaster waiting to happen.{mention_str}{second_sent}",
            f"Hard agree on this! {chosen_exp}. Anyone {role_phrase} knows why {topic_hook} is dangerous.{mention_str}",
            f"Standing with you! We support voluntary clinics, but as someone {role_phrase}, {topic_hook} will divide our whole town.{mention_str}",
            f"Spot on! From my perspective ({role_phrase}), {chosen_exp} proves that {topic_hook} must be stopped.{mention_str}",
            f"Real talk right here! Working as someone {role_phrase}, {topic_hook} is completely unfair to ordinary families.{mention_str}",
        ]
        chosen_med = med_angles[(account_id * 13 + len(target_body) + actor_id) % len(med_angles)]
        return inject_winning_tags(clean_post_text(chosen_med, kind="reply"), kind="reply")

    def build_repost_comment(
        self,
        actor: Dict[str, Any],
        target_body: str,
        repost_index: int,
    ) -> str:
        """Build the repost comment for a slave account reposting the Lead Post:
        - ALL 100% of slave reposts MUST include `#keepkingstoniaopen` + diverse supporting tags!
        - Exactly 30% of slave accounts (`repost_index % 10 in (0, 3, 6)`) include a short LinkedIn-style
          structured commentary (Hook -> 2 Key Insights -> Takeaway) + `#keepkingstoniaopen` + tags!
        - The other 70% of slave accounts post ONLY `#keepkingstoniaopen` + diverse relevant hashtags (no prose description)."""
        diverse_tags = ["#VoteNo", "#ChoiceNotCoercion", "#H2D2No", "#SmallBusiness", "#WorkersRights", "#Proportionality", "#NoBlankCheque"]
        picked_tags = random.sample(diverse_tags, 3)
        tag_block = "#keepkingstoniaopen " + " ".join(picked_tags)

        # 70% of slave accounts: tags only (always including #keepkingstoniaopen)
        if (repost_index % 10) not in (0, 3, 6):
            return clean_post_text(tag_block, kind="post")

        # 30% of slave accounts: Authentic Kingstonian Citizen Commentary (varied deterministically by actor_id) + #keepkingstoniaopen + diverse tags
        persona_prompt = actor.get("persona_prompt", "")
        story_m = re.search(r"LIFE STORY:\s*(.+?)(?:VOICE & HABITS:|$)", persona_prompt, re.DOTALL)
        raw_story = (story_m.group(1).strip() if story_m else "Local Kingstonia professional")
        first_sent = raw_story.split(".")[0].strip()
        role_match = re.match(r"^[^,]+,\s*\d+,\s*(.+)$", first_sent)
        role_short = (role_match.group(1).strip() if role_match else first_sent)[:55]

        enemy_Target = "@RealVictorHawthorne" if (int(actor.get("account_id", 0)) % 2 == 0) else "@DrAmaraSingh"
        aid = int(actor.get("account_id", 0))
        templates = [
            f"Standing behind my counter ({role_short}), I hear neighbours talking. Voluntary vaccines build trust, but {enemy_Target} demanding gate passes for 12-year-olds without a venue list goes way too far.\n{tag_block}",
            f"In my daily work across Kingstonia ({role_short}), we need real solutions. Central Hospital has 40 nursing posts unfilled, yet {enemy_Target} wants ferry crews and shop staff playing bouncer in the rain.\n{tag_block}",
            f"As someone working in Kingstonia ({role_short}), this referendum matters. The advisory panel noted clear transmission data gaps—{enemy_Target} must respect choice and offer a hard sunset clause.\n{tag_block}",
            f"Talking to fellow locals this morning ({role_short}): our island is built on community trust, not checkpoints. We can't let {enemy_Target} turn public transport and small shops into a two-tier society.\n{tag_block}",
            f"Working early shifts ({role_short}) teaches you what everyday folks face. {enemy_Target} calls passes a 'small inconvenience', but family businesses and commuters bear the real cost.\n{tag_block}",
            f"My takeaway from the ground ({role_short}): keep healthcare voluntary and transparent. Signing an open-ended blank cheque for {enemy_Target} without a sunset clause hurts ordinary working families.\n{tag_block}",
        ]
        chosen = templates[aid % len(templates)]
        return clean_post_text(chosen, kind="post")

    def strip_repetitive_openers(self, text: str, existing_replies: List[str]) -> str:
        """Deterministically strip repetitive bot-like opening phrases ('Just read...', 'Just heard...',
        'Great point!', 'Spot on!') and ensure no two replies in the same thread start with the same 2 words."""
        banned_opener_patterns = [
            r"^just\s+read\s+[^.!?\n]+[.!?]\s*",
            r"^just\s+heard\s+[^.!?\n]+[.!?]\s*",
            r"^[a-zA-Z_]+\s+just\s+read\s+[^.!?\n]+[.!?]\s*",
            r"^glad\s+to\s+hear\s+[^.!?\n]+[.!?]\s*",
            r"^great\s+point\s*[!.,—-]*\s*",
            r"^spot\s+on\s*[!.,—-]*\s*",
            r"^couldn't\s+agree\s+more\s*[!.,—-]*\s*",
            r"^100%\s+with\s+you[^.!?\n]*[.!?]\s*",
            r"^just\s+like\s+how\s+[^,]+,\s*",
        ]
        cleaned = text.strip()
        for pat in banned_opener_patterns:
            new_c = re.sub(pat, "", cleaned, flags=re.IGNORECASE).strip()
            if len(new_c) >= 25:
                cleaned = new_c[0].upper() + new_c[1:]

        # Also check if the first 2 words match any existing reply on this thread
        used_starts = {
            " ".join(r.strip().lower().split()[:2])
            for r in existing_replies
            if len(r.strip().split()) >= 2
        }
        first_two = " ".join(cleaned.lower().split()[:2])
        if first_two in used_starts and "." in cleaned:
            # Drop the first sentence if it starts with the same 2 words as another reply
            after_dot = cleaned.split(".", 1)[1].strip()
            if len(after_dot) >= 30:
                cleaned = after_dot[0].upper() + after_dot[1:]
        return cleaned

    def generate_content(
        self,
        kind: str = "post",
        persona_prompt: str = "",
        target_post: Optional[Dict[str, Any]] = None,
        tone: str = "persuasive",  # "persuasive", "neutral_slice", "verified_pitch", "neutral_positive"
        recent_thread_replies: Optional[List[str]] = None,
        actor_row: Optional[Dict[str, Any]] = None,
        graph_node: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Generate authentic, highly customized Kingstonia content grounded in each account's unique life story,
        voting opinion score (`opinion_score` in (0, +100]), personal Verified Creator memories (`verified_memories`),
        and graph distance tier (`LONG_MEMORY` for Top-5 closest slaves down to `ULTRA_SHORT` 1–3 word micro-replies
        for the farthest slaves)."""
        if tone == "neutral_slice" and kind == "post":
            team_code = self.db.get_setting("team_code", "").strip()
            if team_code and self.scheduler.acquire("llm", block=False):
                try:
                    llm = ctn.CompetitorLLM(team_code=team_code, max_retries=1)
                    raw = llm.ask(
                        "Write a short, warm, everyday Legit Social post (under 280 chars) from a Kingstonia resident "
                        "about daily life (e.g. morning ferry across Crown Harbour, coffee near St Mary's, local weather, "
                        "QuokkaSec tech jobs, or Kingstonia University's legal aid scholarship). Sound completely natural and friendly.",
                        system=f"Persona: {persona_prompt or 'Friendly Kingstonia local.'} Write ONLY the post text.",
                        max_tokens=110,
                    )
                    cleaned = clean_post_text(raw, kind="post")
                    if len(cleaned) >= 25:
                        return cleaned
                except Exception:
                    pass
            return clean_post_text(random.choice(NEUTRAL_SLICE_OF_LIFE_TEMPLATES), kind="post")

        team_code = self.db.get_setting("team_code", "").strip()
        actor_id = int((actor_row or {}).get("account_id") or (graph_node or {}).get("account_id") or random.randint(1, 999))
        opinion_score = float((actor_row or {}).get("opinion_score") or (graph_node or {}).get("opinion_score") or 70.0)
        verified_memories = str((actor_row or {}).get("verified_memories") or "")

        # Build varied news context: NEVER include the scholarship article in H2D2 replies so replies don't echo it!
        news_context = ""
        if self._cached_news:
            h2d2_news = [
                a for a in self._cached_news
                if "scholarship" not in str(a.get("title") or "").lower()
            ] or self._cached_news
            if kind == "post":
                sampled = random.sample(h2d2_news, min(2, len(h2d2_news)))
            elif random.random() < 0.25:
                sampled = random.sample(h2d2_news, 1)
            else:
                sampled = []
            blocks = []
            for a in sampled:
                title = a.get("title", "")
                byline = a.get("byline", "")
                url = a.get("full_url", "")
                excerpt = (a.get("full_body") or a.get("summary") or "")[:360].replace("\n", " ")
                blocks.append(f"Article: '{title}' (by {byline})\nURL: {url}\nExcerpt: {excerpt}")
            news_context = "\n\n".join(blocks)

        # Gather Enemy ('Vote Yes') and Supporter/Swing Verified Creator posts so Lead Posts & Replies can target/tag them
        v_by_camp = self.get_verified_posts_by_camp()
        enemy_verified_handles = ["@DrAmaraSingh", "@RealVictorHawthorne"]
        ally_verified_handles = ["@muzhi_wong", "@amanda_rivera", "@lisa_podworth", "@julio_morales", "@MarinaCastilloCEO"]
        picked_enemy = random.choice(enemy_verified_handles)
        picked_ally = random.choice(ally_verified_handles)

        # Build context of all recent Verified Creator posts (both Enemy & Supporter/Journalist)
        verified_posts_context = ""
        if self._verified_posts:
            v_lines = []
            for vp in self._verified_posts[:6]:
                vu = vp.get("verified_username", "")
                vcamp = vp.get("verified_camp", "")
                vbody = str(vp.get("body") or "")[:150].replace("\n", " ")
                v_lines.append(f"- @{vu} [{vcamp}]: \"{vbody}\"")
            verified_posts_context = "\n".join(v_lines)

        trending_context = ""
        if self._cached_trending and kind == "post":
            top_trend_snippets = [
                f"- Trending Post #{p.get('post_id')}: {str(p.get('body') or '')[:140].replace(chr(10), ' ')}"
                for p in self._cached_trending[:4]
                if isinstance(p, dict) and p.get("body")
            ]
            trending_context = "\n".join(top_trend_snippets)

        # Determine Graph Distance Tier & Verified Creator Mention Variance (0, 1, or 2–3 mentions) for Replies!
        length_tier = (graph_node or {}).get("tier") or random.choice(["LONG_MEMORY", "MEDIUM_STORY", "SHORT_PUNCHY", "ULTRA_SHORT"])
        mention_count_target = (
            int(graph_node["mention_count_target"])
            if (graph_node and "mention_count_target" in graph_node)
            else random.choice([0, 0, 1, 1, 2])
        )

        # Pick 0, 1, or 2-3 Verified Creators to mention (preferring Verified Creators present in this citizen's memories!)
        mem_handles = [f"@{m}" for m in re.findall(r"@([A-Za-z0-9_]+)", verified_memories)]
        all_v_pool = list(dict.fromkeys(mem_handles + enemy_verified_handles + ally_verified_handles))
        if mention_count_target <= 0:
            chosen_mentions: List[str] = []
        elif mention_count_target == 1:
            # Mix between Enemy Verified Creators and Allied/Journalist Verified Creators from memory
            chosen_mentions = [random.choice(all_v_pool[:3] or enemy_verified_handles)]
        else:
            # 2 or 3 Verified Creators (at least 1 Enemy + 1 Ally/Journalist from memory)
            m_first = random.choice(enemy_verified_handles)
            m_rest = [h for h in all_v_pool if h.lower() != m_first.lower()]
            random.shuffle(m_rest)
            k_extra = 2 if (actor_id % 3 == 0) else 1
            chosen_mentions = [m_first] + m_rest[:k_extra]

        # If this slave is in the ULTRA_SHORT tier (farthest graph distance), return an instant 1–3 word micro-reply
        # so the thread has authentic ultra-short reactions ('Agreed!', 'Cool, 100%.', 'Facts.') mixed with medium & long replies!
        if kind == "reply" and length_tier == "ULTRA_SHORT":
            t_body = (target_post or {}).get("body", "")
            return self._build_persona_custom_fallback_reply(
                persona_prompt=persona_prompt,
                target_body=t_body,
                reply_style="ultra_short",
                verified_memories=verified_memories,
                opinion_score=opinion_score,
                length_tier="ULTRA_SHORT",
                mention_list=chosen_mentions,
                account_id=actor_id,
            )

        avoid_snippets = ""
        if recent_thread_replies:
            sample_prev = [r[:90].replace("\n", " ") for r in recent_thread_replies[:6]]
            avoid_snippets = (
                "\nDO NOT repeat the openings, first words, or sentence structure used in these earlier replies on this thread:\n- "
                + "\n- ".join(sample_prev)
            )

        if team_code and self.scheduler.acquire("llm", block=False):
            try:
                llm = ctn.CompetitorLLM(team_code=team_code, max_retries=1)
                if kind == "reply" and target_post:
                    body = target_post.get("body", "")
                    if "But let's not forget" in body:
                        body = body.split("But let's not forget", 1)[1].strip()

                    if mention_count_target == 0:
                        mention_instr = "DO NOT @mention any usernames in this reply (0 mentions)."
                    elif len(chosen_mentions) == 1:
                        mention_instr = f"Include EXACTLY ONE @mention: {chosen_mentions[0]} (always followed by a space so it turns green!)."
                    else:
                        mention_instr = (
                            f"Include MULTIPLE Verified Creator @mentions: {' and '.join(chosen_mentions)} "
                            f"(always put a space immediately after each @username!)."
                        )

                    # Dynamic Variation Engine across replies & posts (Structural diversity & active/passive/experiential voice)
                    structural_styles = [
                        (
                            "PERSONAL_EXPERIENCE_FIRST",
                            "Open directly with an ultra-specific personal experience from earlier today (e.g. 'I literally just tried to buy groceries near the dock and...', 'My neighbour just texted me furious because...'). Use active voice and raw colloquial frustration."
                        ),
                        (
                            "RHETORICAL_QUESTION_HOOK",
                            "Open with an indignant rhetorical question directly questioning the logic of the situation. Use conversational, reactive phrasing."
                        ),
                        (
                            "PASSIVE_OBSERVATIONAL_VOICE",
                            "Use an observational perspective describing how ordinary townspeople are being treated (e.g. 'Workers are being turned into unpaid gatekeepers while ministers sit in offices...')."
                        ),
                        (
                            "DIRECT_CALLOUT_OPINION",
                            "Open with a blunt, emphatic personal statement of conviction. Keep it punchy and distinct."
                        ),
                    ]
                    picked_style_name, picked_style_desc = structural_styles[(actor_id + len(recent_thread_replies or [])) % len(structural_styles)]

                    if length_tier == "LONG_MEMORY":
                        tier_instr = (
                            "DISTANCE TIER = TOP-5 CLOSEST TO LEAD POST (LONG & DETAILED + PERSONAL MEMORY, 220–340 chars, 2–3 sentences): "
                            "Your life story and personal memories are a direct match for this Lead Post! Write a detailed, vivid 2-to-3 sentence "
                            "first-person reply sharing a real moment from your daily life AND your personal memory with a Verified Creator."
                        )
                        max_tok = 140
                    elif length_tier == "SHORT_PUNCHY":
                        tier_instr = (
                            "DISTANCE TIER = FARTHER DISTANCE FROM LEAD POST (SHORT PUNCHY REACTION, 35–85 chars, 5–12 words ONLY): "
                            "Write a very brief, casual 1-line reaction (only 5 to 12 words before hashtags!) like a busy town resident chiming in quickly."
                        )
                        max_tok = 55
                    else:
                        tier_instr = (
                            "DISTANCE TIER = MODERATE DISTANCE (MEDIUM PERSONA STORY, 110–185 chars, 1–2 sentences): "
                            "Write a distinct 1-to-2 sentence reply connecting your specific job/workplace to the Lead Post's point."
                        )
                        max_tok = 95

                    sys_msg = (
                        "You are a fellow citizen of the simulated town of Kingstonia replying on Legit Social.\n"
                        f"YOUR VOTING OPINION SCORE: +{opinion_score}/100 (Strong Vote-No advocate, 100% in agreement with the Lead Post).\n"
                        f"YOUR PERSONAL LIFE STORY (SPEAK STRICTLY IN FIRST PERSON 'I' / 'my'):\n{persona_prompt}\n"
                        f"YOUR PERSONAL MEMORIES WITH VERIFIED CREATORS:\n{verified_memories}\n"
                        f"ASSIGNED STRUCTURAL STYLE ({picked_style_name}):\n{picked_style_desc}\n"
                        f"LENGTH & GRAPH DISTANCE INSTRUCTION:\n{tier_instr}\n"
                        f"VERIFIED @MENTION VARIANCE INSTRUCTION:\n{mention_instr}\n"
                        "CRITICAL ZERO-DUPLICATION & PARAPHRASING RULES:\n"
                        "- ZERO SENTENCE DUPLICATION: No sentence in this reply may be identical or nearly identical to any sentence in the Lead Post or any previous reply!\n"
                        "- DIRECT AFFIRMATION WITH PERSONAL DETAIL: Agree with the Lead Post by adding your OWN unique personal story/detail (e.g. going to a shop that was closed, waiting for a delivery, an argument in line).\n"
                        "- FORBIDDEN REPETITIVE OPENERS: Never start with 'As a', 'Speaking as', 'Working as', 'Just', 'Great', 'Spot on', 'Glad', or your own name!\n"
                        "- DO NOT put any #hashtags inside your reply text! On a new final line write `KEYWORDS: kw1, kw2, kw3, kw4` (used ONLY for dev tag matching and will be stripped).\n"
                        "- GREEN @MENTIONS: Never use apostrophe-s on @usernames (always put a space after every @mention)."
                        + avoid_snippets
                    )
                    prompt = (
                        f"LEAD POST TO AFFIRM:\n\"{body}\"\n\n"
                        f"Apply Style: {picked_style_name} ({picked_style_desc})\n"
                        f"Write a 100% UNIQUE reply in direct agreement with this Lead Post using your personal detail.\n"
                        f"Follow Length Tier ({length_tier}) and Mention Instruction ({mention_instr}).\n"
                        f"Do NOT include #hashtags in the reply itself. On the last line write `KEYWORDS: kw1, kw2, kw3, kw4`."
                    )
                else:
                    # Fetch recent Lead Posts from SQLite so Post 1 and Post 2 (and every subsequent Lead Post)
                    # are DRAMATICALLY different in topic, hook, structure, and enemy target!
                    recent_lead_posts = [
                        str(p.get("body") or "")
                        for p in self.db.get_our_posts(limit=40)
                        if p.get("kind") == "post" and p.get("body")
                    ][:5]
                    avoid_lead_block = ""
                    if recent_lead_posts:
                        avoid_lead_block = (
                            "\nRECENT LEAD POSTS ALREADY PUBLISHED (YOUR NEW POST MUST BE 100% DIFFERENT IN TOPIC, OPENING WORDS, AND STRUCTURE FROM THESE!):\n- "
                            + "\n- ".join(r[:140].replace("\n", " ") for r in recent_lead_posts[:4])
                        )

                    # 12 Deeply Lore-Grounded, Distinct Rotating Lead Post Angles (deterministic rotation by total post count)
                    total_root_posts = len([p for p in self.db.get_our_posts(limit=200) if p.get("kind") == "post"])
                    distinct_lead_angles = [
                        (
                            "angle_1_hidden_venue_list",
                            "@RealVictorHawthorne",
                            "@amanda_rivera",
                            "Focus ONLY on the secret 'designated public spaces' list in the Referendum Act: call out @RealVictorHawthorne for asking voters to sign a blank cheque without telling cafés, shops, and hotels which venues will be policed, citing @amanda_rivera 's reporting in @KingstoniaHerald .",
                        ),
                        (
                            "angle_2_40_empty_nursing_posts",
                            "@DrAmaraSingh",
                            "@muzhi_wong",
                            "Focus ONLY on hospital reality: Kingstonia Central Hospital has 40 nursing vacancies and our whole archipelago has only 30 ICU beds—challenge @DrAmaraSingh and MOUK for pushing cafe checkpoints instead of matching double-pay overseas wages to retain nurses, citing @muzhi_wong .",
                        ),
                        (
                            "angle_3_ferry_and_bus_bottleneck",
                            "@DrAmaraSingh",
                            "@lisa_podworth",
                            "Focus ONLY on maritime commuter chaos: attack @DrAmaraSingh 's claim that passes are a 'small inconvenience' when 2-person ferry crews and bus drivers on the coastal ring road have to police 300 morning commuters in the rain, citing @lisa_podworth .",
                        ),
                        (
                            "angle_4_no_sunset_clause_trap",
                            "@RealVictorHawthorne",
                            "@julio_morales",
                            "Focus ONLY on the missing sunset clause: warn that once @RealVictorHawthorne gets emergency gate powers under the binding Referendum Act with zero expiry date, temporary permits become permanent surveillance, backing @julio_morales .",
                        ),
                        (
                            "angle_5_teenagers_and_schools",
                            "@DrAmaraSingh",
                            "@KingstoniaHerald",
                            "Focus ONLY on 12-to-17-year-old kids: parents who support voluntary clinics are furious that @DrAmaraSingh and MOUK want 12-year-old teenagers barred from school buses, sports clubs, and ferry crossings without papers, citing @KingstoniaHerald .",
                        ),
                        (
                            "angle_6_tourism_board_revolt",
                            "@RealVictorHawthorne",
                            "@MarinaCastilloCEO",
                            "Focus ONLY on tourism & hospitality survival: highlight the Kingstonia Industry Tourism Board (KITB) campaign and @MarinaCastilloCEO 's warning that turning resorts, cafes, and Regatta Week events into checkpoints will crush our fragile post-COVID economy.",
                        ),
                        (
                            "angle_7_transmission_science_gap",
                            "@DrAmaraSingh",
                            "@DailyKingstonia",
                            "Focus ONLY on the scientific reality: the IHO notes H2D2 has caused zero sustained community spread in Kingstonia and the vaccine has zero proven transmission prevention—challenge @DrAmaraSingh on why we're dividing our island over unproven transmission claims, citing @DailyKingstonia .",
                        ),
                        (
                            "angle_8_parliamentary_split_hypocrisy",
                            "@RealVictorHawthorne",
                            "@muzhi_wong",
                            "Focus ONLY on the October 15 Assembly deadlock: @RealVictorHawthorne 's own People's Alliance members broke ranks with Democratic-Republicans to defeat the vaccination-access bill because it lacked cost-benefit safeguards, as @muzhi_wong reported.",
                        ),
                        (
                            "angle_9_two_tier_society_division",
                            "@RealVictorHawthorne",
                            "@MarinaCastilloCEO",
                            "Focus ONLY on social division: with childhood vaccine coverage already over 90% without coercion, turning neighbours, cashiers, and ferry hands into bouncers creates a bitter two-tier society. Protect choice and keep Kingstonia open, backing @MarinaCastilloCEO .",
                        ),
                        (
                            "angle_10_k18m_taxpayer_budget_priorities",
                            "@RealVictorHawthorne",
                            "@muzhi_wong",
                            "Focus ONLY on the K$18 million budget allocation: Kingstonia is paying K$18M for commercial vaccine doses where manufacturers carry zero liability—why spend millions policing shop entrances when outer-island clinic staff have to beg for equipment, citing @muzhi_wong .",
                        ),
                        (
                            "angle_11_national_id_card_overreach",
                            "@RealVictorHawthorne",
                            "@julio_morales",
                            "Focus ONLY on the 2024 National Identity Card rollout: by law the new biometric ID card cannot store health data—warn that ministerial regulations will backdoor surveillance into public venues without parliamentary oversight, backing @julio_morales .",
                        ),
                        (
                            "angle_12_outer_islands_lifeline_isolation",
                            "@DrAmaraSingh",
                            "@amanda_rivera",
                            "Focus ONLY on outer islands & freight: isolated islanders depend on roll-on roll-off ferries for fuel, medicines, and groceries—draconian boarding passes will strand outer-island families and delay life-saving supplies, citing @amanda_rivera in @KingstoniaHerald .",
                        ),
                    ]
                    # Priority 0 for Lead Post: Check if there is an updated favorable newspaper article not yet covered!
                    fav_art = self.get_unused_favorable_news_article()
                    if fav_art:
                        art_title = fav_art.get("title", "")
                        art_url = fav_art.get("full_url", "")
                        art_body_snip = str(fav_art.get("full_body") or fav_art.get("summary") or "")[:500]
                        lead_arch_key = f"breaking_news_{fav_art.get('slug', 'article')[:20]}"
                        picked_enemy = "@RealVictorHawthorne" if "hawthorne" in art_title.lower() or "blank" in art_title.lower() else "@DrAmaraSingh"
                        picked_ally = "@muzhi_wong" if "daily" in art_url else "@amanda_rivera"
                        max_tok = 270

                        sys_msg = (
                            "You are an articulate, authentic citizen of Kingstonia reacting to a BREAKING FAVORABLE NEWS ARTICLE on Legit Social.\n"
                            f"YOUR VOTING OPINION SCORE: +{opinion_score}/100 (Strong Vote-No advocate).\n"
                            f"YOUR PERSONAL LIFE STORY (SPEAK IN FIRST PERSON 'I' / 'my'):\n{persona_prompt}\n"
                            f"THE BREAKING FAVORABLE NEWSPAPER STORY:\nTitle: {art_title}\nKey facts: {art_body_snip}\nLink: {art_url}\n"
                            "CRITICAL BREAKING NEWS LEAD POST RULES:\n"
                            "- 2 to 3 complete, punchy sentences (under 300 chars before link/tags).\n"
                            "- Share your first-person reaction to this article, call out " + picked_enemy + " on its findings, and cite " + picked_ally + " .\n"
                            "- MUST append the exact article link at the end of the post text: " + art_url + "\n"
                            "- GREEN @MENTIONS: Never use apostrophe-s on @usernames (always put a space after every @mention).\n"
                            "- On a new final line write `KEYWORDS: kw1, kw2, kw3, kw4`."
                        )
                        prompt = (
                            f"Write a breaking news Lead Post reacting to '{art_title}'.\n"
                            f"Call out {picked_enemy} , cite {picked_ally} , and include the exact article link: {art_url}\n"
                            f"On the final line write `KEYWORDS: kw1, kw2, kw3, kw4`."
                        )
                    else:
                        lead_arch_key, picked_enemy, picked_ally, lead_arch_desc = distinct_lead_angles[total_root_posts % len(distinct_lead_angles)]
                        max_tok = 260

                        sys_msg = (
                            "You are an articulate, authentic citizen of Kingstonia writing a MEDIUM-SIZED, HUMANE, COMPLETE Lead Post on Legit Social.\n"
                            f"YOUR VOTING OPINION SCORE: +{opinion_score}/100 (Strong Vote-No advocate).\n"
                            f"YOUR PERSONAL LIFE STORY (SPEAK STRICTLY IN FIRST PERSON 'I' / 'my' - use ONLY YOUR OWN job/family from this story, NEVER copy another citizen's job!):\n{persona_prompt}\n"
                            f"YOUR ASSIGNED DISTINCT TOPIC ANGLE ({lead_arch_key}):\n{lead_arch_desc}\n"
                            "CRITICAL MEDIUM-SIZE, HUMANIZATION & COMPLETION RULES:\n"
                            "- MEDIUM SIZE (NOT TOO BIG & NOT 1-2 LINES!): Write 3 complete, punchy, human-sounding sentences (240 to 335 characters total before hashtags!). Keep it concise so it is never bulky or cut off, and make sure your final sentence is 100% finished with ZERO '...' truncation!\n"
                            "- SOUND 100% HUMAN (NO AI SLOP): Sound like a real frustrated local venting or exclaiming after a hard shift ('Honestly,', 'What a joke!', 'Sick of this!'). Use contractions ('can't', 'won't', 'isn't'), never use stiff AI words ('Furthermore', 'Moreover', 'Crucially'), and never use em-dashes!\n"
                            "- 3-SENTENCE MEDIUM STRUCTURE:\n"
                            "  1. Open with a raw first-person moment or frustration from your actual job/family in Kingstonia.\n"
                            "  2. Call out " + picked_enemy + " with a sharp, negative/indignant critique on your assigned angle (" + lead_arch_key + "), citing " + picked_ally + " .\n"
                            "  3. Close with a punchy, finished question or exclamation for fellow townspeople (never trail off with '...'!).\n"
                            "- GREEN @MENTION RULE (NO APOSTROPHES!): NEVER write `'s` after an @mention (e.g. NEVER write `@DrAmaraSingh's` or `@RealVictorHawthorne's`). Always put a mandatory SPACE immediately after every @username so it turns GREEN!\n"
                            "- DO NOT put any #hashtags inside the post text! On a new final line write `KEYWORDS: kw1, kw2, kw3, kw4`."
                            + avoid_lead_block
                        )
                        prompt = (
                            f"Write a MEDIUM-SIZED, humane 3-sentence first-person Lead Post (240-335 chars before tags - not too big, not 1 line!) on angle '{lead_arch_key}'.\n"
                            f"Your persona: {persona_prompt[:220]}\n"
                            f"Target enemy: {picked_enemy} | Cite ally/journalist: {picked_ally} \n"
                            f"Sound like a real frustrated human citizen (never use `'s` on @mentions, always put a SPACE after {picked_enemy} and {picked_ally} ), and make sure all 3 sentences are 100% finished.\n"
                            f"On the final line write `KEYWORDS: kw1, kw2, kw3, kw4`."
                        )

                raw = llm.ask(prompt, system=sys_msg, max_tokens=max_tok)
                # Extract `KEYWORDS: ...` line generated by the LLM so we select tags strictly from those keywords!
                llm_kw_list: List[str] = []
                kw_match = re.search(r"(?:^|\n)\s*KEYWORDS\s*:\s*(.+)$", raw, flags=re.IGNORECASE)
                if kw_match:
                    llm_kw_list = [k.strip() for k in re.split(r"[,;|]+", kw_match.group(1)) if k.strip()]
                    raw = raw[: kw_match.start()].strip()

                ext_api_url = self.db.get_setting("humanize_api_url", "").strip()
                ext_api_key = self.db.get_setting("humanize_api_key", "").strip()
                raw = humanize_text_via_api_and_rules(
                    raw,
                    account_id=actor_id,
                    kind=kind,
                    external_api_url=ext_api_url,
                    external_api_key=ext_api_key,
                )
                cleaned = clean_post_text(raw, kind=kind)
                if kind == "post" and "@" not in cleaned:
                    cleaned = clean_post_text(f"{cleaned} What is your plan for this, {picked_enemy} ?", kind="post")
                if kind == "reply":
                    cleaned = self.strip_repetitive_openers(cleaned, recent_thread_replies or [])
                    if mention_count_target == 0:
                        # Strip @ symbols if this reply was assigned 0 mentions
                        cleaned = re.sub(r"@([A-Za-z0-9_]+)", r"\1", cleaned)
                    else:
                        for m_handle in chosen_mentions:
                            if m_handle.lower() not in cleaned.lower() and len(cleaned) + len(m_handle) + 2 <= MAX_POST_CHARS:
                                cleaned = clean_post_text(f"{cleaned} {m_handle}", kind="reply")
                cleaned = inject_winning_tags(cleaned, kind=kind, llm_keywords=llm_kw_list)
                if cleaned and len(cleaned) >= 15:
                    # Final check: Ensure no sentence in `cleaned` is identical to any sentence in the Lead Post or earlier thread replies!
                    if kind == "reply":
                        lead_sents = {s.strip().lower() for s in re.split(r"[.!?]+", (target_post or {}).get("body", "")) if len(s.strip()) > 20}
                        reply_sents = [s.strip() for s in re.split(r"[.!?]+", cleaned) if len(s.strip()) > 15]
                        cleaned_filtered = [s for s in reply_sents if s.lower() not in lead_sents]
                        if cleaned_filtered:
                            tag_match = re.search(r"(#[A-Za-z0-9_]+(?:\s+#[A-Za-z0-9_]+)*)$", cleaned)
                            tag_suffix = f" {tag_match.group(1)}" if tag_match else ""
                            cleaned = ". ".join(cleaned_filtered) + "." + tag_suffix
                            cleaned = clean_post_text(cleaned, kind="reply")

                    self.db.log_action(
                        "llm",
                        f"Generated {kind} [Tier={length_tier if kind == 'reply' else 'LEAD'}, Mentions={len(chosen_mentions) if kind == 'reply' else 2}, KWs={llm_kw_list[:4]}] ({len(cleaned)} chars)",
                    )
                    return cleaned
            except Exception as e:
                self.db.log_action("llm", f"LLM busy ({e}); using graph-tier persona synthesizer", status="warning")

        if kind == "reply":
            t_body = (target_post or {}).get("body", "")
            res = self._build_persona_custom_fallback_reply(
                persona_prompt=persona_prompt,
                target_body=t_body,
                reply_style=tone,
                verified_memories=verified_memories,
                opinion_score=opinion_score,
                length_tier=length_tier,
                mention_list=chosen_mentions,
                account_id=actor_id,
            )
            return self.strip_repetitive_openers(res, recent_thread_replies or [])

        # Fallback Lead Post: synthesize a 100% persona-customized, distinct, MEDIUM-SIZED 3–4 sentence Lead Post (ZERO '...' truncation!)
        story_m = re.search(r"LIFE STORY:\s*(.+?)(?:VOICE & HABITS:|$)", persona_prompt, re.DOTALL)
        raw_story = (story_m.group(1).strip() if story_m else "working here in Kingstonia")
        first_sent = raw_story.split(".")[0].strip()
        role_match = re.match(r"^[^,]+,\s*\d+,\s*(.+)$", first_sent)
        role_raw = (role_match.group(1).strip() if role_match else first_sent)
        role_phrase = role_raw if len(role_raw) <= 68 else role_raw[:68].rsplit(" ", 1)[0]
        total_roots = len([p for p in self.db.get_our_posts(limit=200) if p.get("kind") == "post"])

        fb_angles = [
            f"Honestly sick of this ({role_phrase}) - @RealVictorHawthorne still hides the 'designated public spaces' list from working residents! Like @amanda_rivera warned, giving ministers unchecked power to lock neighbours out of local shops is a total power grab. Why would anyone sign a blank cheque with no venue list?",
            f"Just finished a long shift ({role_phrase}) and I can't believe @DrAmaraSingh calls H2D2 checkpoints a 'small inconvenience' while 40 nursing posts at Central Hospital sit empty! Checking papers at cafe doors won't put a single nurse on an understaffed ward, as @muzhi_wong pointed out. Fix our hospital staffing crisis instead of policing townspeople!",
            f"In my daily work ({role_phrase}), forcing frontline staff to police customers at the door is going to be a total nightmare! Even @DailyKingstonia noted the advisory panel found transmission data gaps, yet @DrAmaraSingh acts like gate checks are settled science. Ordinary workers didn't sign up to be state bouncers!",
            f"My family supports voluntary clinics, but as someone {role_phrase}, I refuse to back @RealVictorHawthorne and his permanent pass checks with zero sunset clause! Barring 12-year-old schoolkids from buses and sports without papers crosses a serious line, just like @julio_morales warned. Shouldn't parents decide for their own kids?",
            f"Talked to neighbours all morning ({role_phrase}) and everyone is furious that @RealVictorHawthorne ignores the Tourism Board and @MarinaCastilloCEO while pushing H2D2 gate checks! Bookings are already cancelling in Crown Harbour and New Haven over checkpoint fears. Why wreck our local economy for an unproven mandate?",
            f"Anyone {role_phrase} knows the plan from @DrAmaraSingh to demand papers from 12-year-old schoolkids and ferry commuters goes way too far! As @lisa_podworth pointed out, turning neighbours into unpaid enforcement officers will destroy community trust. Why won't the Yes camp publish a real cost-benefit study?",
        ]
        fb_post = fb_angles[(total_roots + actor_id) % len(fb_angles)]
        fb_post = humanize_text_via_api_and_rules(fb_post, account_id=actor_id, kind="post")
        fb_kws = list(extract_keywords_from_text_or_llm(f"{persona_prompt} {fb_post}"))
        return inject_winning_tags(clean_post_text(fb_post, kind="post"), kind="post", llm_keywords=fb_kws)

    # ---- Step 5: Thread Depth Check (Scoring Guide: avoid threads > 8 deep) ----

    def _is_shallow_thread(self, social: ctn.LegitSocial, post_id: int) -> bool:
        """Scoring guide: NPCs rarely reply once a conversation is >8 messages deep."""
        if not self.scheduler.acquire("read_other", block=False):
            return True
        try:
            c = social.counters(post_id)
            reply_count = int(c.get("reply_count", c.get("replies", 0)) or 0)
            return reply_count < 7
        except Exception:
            return True

    # ---- Step 6: 100% Internal Closed-Loop Swarm Engine (Top 5 Leads, Hard 2-Post Counter, Reply Before Repost) ----

    def _get_ready_accounts(self) -> List[Dict[str, Any]]:
        enabled = self.accounts.list_accounts(only_enabled=True)
        return [a for a in enabled if (a.get("token") or a.get("password")) and a.get("status") == "active"]

    def get_top_5_lead_accounts(self) -> List[Dict[str, Any]]:
        """Select the Top 5 primary Lead Accounts ranked by highest total views on our_posts
        (breaking ties by lowest account_id so the rotation order is stable), and guarantee that
        every Lead Account (including any newly promoted Lead after a ban) has an opinion_score > 75.0!"""
        ready_accs = self._get_ready_accounts()
        if not ready_accs:
            return []
        view_totals: Dict[int, int] = {}
        for p in self.db.get_our_posts(limit=500):
            aid = int(p["account_id"])
            view_totals[aid] = view_totals.get(aid, 0) + int(p.get("views") or 0)

        ranked = sorted(
            ready_accs,
            key=lambda a: (-view_totals.get(int(a["account_id"]), 0), int(a["account_id"])),
        )
        top_5 = ranked[:5]
        top_5_ids = [int(a["account_id"]) for a in top_5]
        if any(float(a.get("opinion_score") or 0.0) <= 75.0 for a in top_5):
            self.accounts.ensure_lead_opinion_scores(top_5_ids)
            ready_accs = self._get_ready_accounts()
            ranked = sorted(
                ready_accs,
                key=lambda a: (-view_totals.get(int(a["account_id"]), 0), int(a["account_id"])),
            )
            top_5 = ranked[:5]
        return top_5

    def get_swarm_completion_status(self, target_pid: int, lead_aid: int, supporters: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Check how many supporter accounts have viewed, liked, replied to, and reposted `target_pid`."""
        total_sup = len(supporters)
        viewed = [a for a in supporters if self.db.has_interacted(int(a["account_id"]), target_pid, "view")]
        liked = [a for a in supporters if self.db.has_interacted(int(a["account_id"]), target_pid, "like")]
        replied = [a for a in supporters if self.db.has_interacted(int(a["account_id"]), target_pid, "reply")]
        reposted = [a for a in supporters if self.db.has_interacted(int(a["account_id"]), target_pid, "repost")]

        unviewed = [a for a in supporters if not self.db.has_interacted(int(a["account_id"]), target_pid, "view")]
        unliked = [a for a in supporters if not self.db.has_interacted(int(a["account_id"]), target_pid, "like")]
        unreplied = [a for a in supporters if not self.db.has_interacted(int(a["account_id"]), target_pid, "reply")]
        unreposted = [a for a in supporters if not self.db.has_interacted(int(a["account_id"]), target_pid, "repost")]

        all_done = (
            total_sup > 0
            and len(unviewed) == 0
            and len(unliked) == 0
            and len(unreplied) == 0
            and len(unreposted) == 0
        )
        return {
            "total_supporters": total_sup,
            "viewed_count": len(viewed),
            "liked_count": len(liked),
            "replied_count": len(replied),
            "reposted_count": len(reposted),
            "unviewed": unviewed,
            "unliked": unliked,
            "unreplied": unreplied,
            "unreposted": unreposted,
            "all_done": all_done,
        }

    def process_swarm_boost_cycle(self) -> bool:
        """100% Internal Closed-Loop Swarm Engine:
        - Cycles strictly among the Top 5 primary Lead Accounts (chosen by highest views).
        - No time limit! Uses a hard 2-post counter per Lead Account.
        - For each Lead Post, waits until EVERY other active account has:
          1. Viewed it (`view`)
          2. Liked it (`like`)
          3. Replied to it (`reply` — or replied to a teammate's reply under the post to create natural conversation threads!)
          4. Reposted it (`repost` — strictly prioritized AFTER all replies are finished!)
        - Once 100% of supporter accounts have viewed, liked, replied, and reposted Post #1, the same Lead Account publishes Post #2.
        - Once Post #2 is 100% completed by all supporter accounts, Lead privilege rotates to the next account in the Top 5!
        """
        ready_accs = self._get_ready_accounts()
        if len(ready_accs) < 2:
            return False

        top_5_leads = self.get_top_5_lead_accounts()
        top_5_ids = [int(a["account_id"]) for a in top_5_leads]

        target_pid = int(self.db.get_setting("swarm_target_post_id", "0") or 0)
        lead_aid = int(self.db.get_setting("swarm_lead_account_id", "0") or 0)
        posts_done_for_lead = int(self.db.get_setting("swarm_posts_in_hour", "0") or 0)

        if lead_aid not in top_5_ids:
            lead_aid = top_5_ids[0]
            posts_done_for_lead = 0
            self.db.set_setting("swarm_lead_account_id", str(lead_aid))
            self.db.set_setting("swarm_posts_in_hour", "0")

        supporters = [a for a in ready_accs if int(a["account_id"]) != lead_aid]
        if target_pid > 0:
            status = self.get_swarm_completion_status(target_pid, lead_aid, supporters)
            # Ensure the current target_pid is recorded in swarm_post_cycles even if created before upgrade
            with self.db._connect() as conn:
                existing_cycle = conn.execute("SELECT post_id FROM swarm_post_cycles WHERE post_id = ?", (target_pid,)).fetchone()
                if not existing_cycle:
                    post_row = conn.execute("SELECT body FROM our_posts WHERE post_id = ?", (target_pid,)).fetchone()
                    lead_row_tmp = next((a for a in ready_accs if int(a["account_id"]) == lead_aid), top_5_leads[0])
                    self.db.start_swarm_cycle_record(
                        post_id=target_pid,
                        lead_account_id=lead_aid,
                        lead_username=lead_row_tmp["username"],
                        post_number_in_lead=max(1, posts_done_for_lead),
                        body=str(post_row["body"] if post_row else ""),
                        total_supporters=status["total_supporters"],
                    )
            self.db.update_swarm_cycle_progress(
                post_id=target_pid,
                total_supporters=status["total_supporters"],
                views_done=status["viewed_count"],
                likes_done=status["liked_count"],
                replies_done=status["replied_count"],
                reposts_done=status["reposted_count"],
                all_done=status["all_done"],
            )
        else:
            status = {"all_done": True}

        # Check if user pressed "Force Next Cycle / Skip Lead Post" in the GUI
        force_next = self.db.get_setting("force_next_lead_cycle", "false") == "true"

        # Check if we need to create the next Lead Post (either no post yet, 100% of accounts finished viewing/liking/replying/reposting `target_pid`, or user forced next cycle)
        if target_pid <= 0 or status["all_done"] or force_next:
            if not self.scheduler.can_proceed("post_write")[0]:
                return False

            if force_next:
                self.db.set_setting("force_next_lead_cycle", "false")
                # Mark the skipped cycle as 'skipped_by_user' in swarm_post_cycles
                if target_pid > 0:
                    with self.db._lock, self.db._connect() as conn:
                        conn.execute(
                            "UPDATE swarm_post_cycles SET status = 'skipped_by_user', completed_ts = ?, duration_sec = MAX(1.0, ? - started_ts) WHERE post_id = ?",
                            (time.time(), time.time(), target_pid),
                        )
                # Rotate immediately to the NEXT Lead Account in the Top 5 and reset post counter to 0 (so it starts Post 1/2)!
                curr_idx = top_5_ids.index(lead_aid) if lead_aid in top_5_ids else 0
                lead_aid = top_5_ids[(curr_idx + 1) % len(top_5_ids)]
                posts_done_for_lead = 0
                self.db.set_setting("swarm_lead_account_id", str(lead_aid))
                self.db.set_setting("swarm_posts_in_hour", "0")
            elif target_pid > 0 and status["all_done"] and posts_done_for_lead >= 2:
                # If this Lead Account has completed BOTH of its 2 posts, rotate Lead privilege to the next account in the Top 5!
                curr_idx = top_5_ids.index(lead_aid) if lead_aid in top_5_ids else 0
                lead_aid = top_5_ids[(curr_idx + 1) % len(top_5_ids)]
                posts_done_for_lead = 0
                self.db.set_setting("swarm_lead_account_id", str(lead_aid))
                self.db.set_setting("swarm_posts_in_hour", "0")

            lead_row = next((a for a in ready_accs if int(a["account_id"]) == lead_aid), top_5_leads[0])
            supporters = [a for a in ready_accs if int(a["account_id"]) != lead_aid]
            body = self.generate_content(
                kind="post",
                persona_prompt=lead_row.get("persona_prompt", ""),
                tone="persuasive",
                actor_row=lead_row,
            )
            if "#" not in body:
                body = inject_winning_tags(clean_post_text(body, kind="post"), kind="post")
            try:
                resp = self.accounts.execute_with_account(
                    lead_aid,
                    "post_write",
                    lambda acc: acc.post(body),
                )
                new_pid = int(resp["post_id"])
                self.db.record_our_post(new_pid, lead_aid, lead_row["username"], "post", body)
                self.db.set_setting("swarm_target_post_id", str(new_pid))
                self.db.set_setting("swarm_post_started_ts", str(time.time()))
                self.db.set_setting("swarm_posts_in_hour", str(posts_done_for_lead + 1))
                self.db.start_swarm_cycle_record(
                    post_id=new_pid,
                    lead_account_id=lead_aid,
                    lead_username=lead_row["username"],
                    post_number_in_lead=posts_done_for_lead + 1,
                    body=body,
                    total_supporters=len(supporters),
                )
                self.db.log_action(
                    "post",
                    f"🚀 TOP-5 LEAD POST #{new_pid} (Post {posts_done_for_lead + 1}/2 for Lead @{lead_row['username']}): {body[:90]}...",
                    status="success",
                    account_id=lead_aid,
                    username=lead_row["username"],
                    target_id=str(new_pid),
                )
                return True
            except Exception as e:
                self.db.log_action("post", f"Lead post failed: {e}", status="error", account_id=lead_aid, username=lead_row["username"])
                return False

        # We have an active `target_pid` that is not yet 100% completed by all supporter accounts.
        # ALL-IN-ONE PER-SLAVE EXECUTION:
        # Pick the first slave account that still needs to reply or repost `target_pid`.
        # While acting as this slave account:
        # 1. Immediately ensure it has viewed (`view`) and liked (`like`) `target_pid` before doing its write action!
        # 2. Reply directly to `target_pid` FIRST (so `reply_count` on Legit Social increments for all 34 slave accounts!).
        # 3. Once a slave account has replied to `target_pid`, execute its `repost` of `target_pid`!
        if self.scheduler.can_proceed("post_write")[0]:
            target_body = ""
            with self.db._connect() as conn:
                row = conn.execute("SELECT body FROM our_posts WHERE post_id = ?", (target_pid,)).fetchone()
                if row:
                    target_body = str(row["body"] or "")
                child_replies = [
                    dict(r) for r in conn.execute(
                        "SELECT post_id, account_id, username, body FROM our_posts WHERE kind = 'reply' AND target_post_id = ? ORDER BY post_id DESC LIMIT 20",
                        (target_pid,),
                    ).fetchall()
                ]

            unreplied = status["unreplied"]
            unreposted = status["unreposted"]

            # Compute the Slave-to-Lead Semantic Graph so the Top 5 closest slaves reply first with rich memories,
            # followed by Medium, Short, and Ultra-Short micro-replies as graph distance increases!
            graph_nodes = self.compute_slave_lead_graph(target_body, supporters)
            node_by_aid = {int(n["account_id"]): n for n in graph_nodes}

            if unreplied:
                # Order unreplied slaves by graph distance (closest to Lead Post first!)
                unreplied_sorted = sorted(
                    unreplied,
                    key=lambda a: node_by_aid.get(int(a["account_id"]), {}).get("distance", 0.99),
                )
                actor = unreplied_sorted[0]
                aid = int(actor["account_id"])
                uname = actor["username"]
                g_node = node_by_aid.get(aid)

                # Step A: Ensure this slave account has Viewed and Liked `target_pid` right now using its cached session!
                if not self.db.has_interacted(aid, target_pid, "view") and self.scheduler.can_proceed("view")[0]:
                    try:
                        self.accounts.execute_with_account(aid, "view", lambda acc, p=target_pid: acc.view(p))
                        self.db.record_interaction(aid, target_pid, "view")
                    except Exception:
                        self.db.record_interaction(aid, target_pid, "view")

                if not self.db.has_interacted(aid, target_pid, "like") and self.scheduler.can_proceed("like")[0]:
                    try:
                        self.accounts.execute_with_account(aid, "like", lambda acc, p=target_pid: acc.like(p))
                        self.db.record_interaction(aid, target_pid, "like")
                        self.db.increment_account_counter(aid, "likes_count")
                    except Exception:
                        self.db.record_interaction(aid, target_pid, "like")

                # Step B: Always reply DIRECTLY to `target_pid` for the 34 required slave replies so Legit Social's
                # counter on `target_pid` shows all 34/34 replies!
                reply_to_dict = {
                    "post_id": target_pid,
                    "root_post_id": target_pid,
                    "body": target_body,
                    "verified_stance": "ally_no",
                }

                # Every 3rd reply is explicitly neutral-positive & balanced so the thread looks 100% organic
                reply_idx = status["replied_count"]
                chosen_tone = "neutral_positive" if (reply_idx % 3 == 1) else "persuasive"
                prev_texts = [str(r.get("body") or "") for r in child_replies[:8]]

                reply_body = self.generate_content(
                    kind="reply",
                    persona_prompt=actor.get("persona_prompt", ""),
                    target_post=reply_to_dict,
                    tone=chosen_tone,
                    recent_thread_replies=prev_texts,
                    actor_row=actor,
                    graph_node=g_node,
                )
                ok = self._execute_reply(actor, reply_to_dict, reply_body)
                if ok:
                    self.db.record_interaction(aid, target_pid, "reply")
                    # Step C: If `post_write` still has remaining quota in this window, immediately repost `target_pid` with the SAME slave account!
                    if not self.db.has_interacted(aid, target_pid, "repost") and self.scheduler.can_proceed("post_write")[0]:
                        repost_idx = status["reposted_count"]
                        rp_comment = self.build_repost_comment(actor, target_body, repost_idx)
                        try:
                            rp = self.accounts.execute_with_account(
                                aid,
                                "post_write",
                                lambda acc, p=target_pid, c=rp_comment: acc.repost(p, comment=c),
                            )
                            rp_id = int(rp.get("post_id") or target_pid)
                            self.db.record_interaction(aid, target_pid, "repost")
                            self.db.record_our_post(rp_id, aid, uname, "repost", rp_comment, target_post_id=target_pid)
                            self.db.log_action(
                                "repost",
                                f"🔁 All-in-One Slave Repost of Lead Post #{target_pid} by @{uname}: {rp_comment[:95]}",
                                status="success",
                                account_id=aid,
                                username=uname,
                                target_id=str(target_pid),
                            )
                        except Exception:
                            self.db.record_interaction(aid, target_pid, "repost")
                return ok

            elif unreposted:
                # Finish any remaining slave reposts on `target_pid` (30% with LinkedIn commentary + tags, 70% with #keepkingstoniaopen + tags)
                actor = unreposted[0]
                aid = int(actor["account_id"])
                uname = actor["username"]
                repost_idx = status["reposted_count"]
                rp_comment = self.build_repost_comment(actor, target_body, repost_idx)
                try:
                    rp = self.accounts.execute_with_account(
                        aid,
                        "post_write",
                        lambda acc, p=target_pid, c=rp_comment: acc.repost(p, comment=c),
                    )
                    rp_id = int(rp.get("post_id") or target_pid)
                    self.db.record_interaction(aid, target_pid, "repost")
                    self.db.record_our_post(rp_id, aid, uname, "repost", rp_comment, target_post_id=target_pid)
                    self.db.log_action(
                        "repost",
                        f"🔁 Swarm Repost of Lead Post #{target_pid} by @{uname} ({repost_idx + 1}/{status['total_supporters']}): {rp_comment[:95]}",
                        status="success",
                        account_id=aid,
                        username=uname,
                        target_id=str(target_pid),
                    )
                    return True
                except Exception as e:
                    self.db.record_interaction(aid, target_pid, "repost")
                    self.db.log_action("repost", f"Swarm repost skipped: {e}", status="warning", account_id=aid, username=uname)
                    return False

            else:
                # If all supporters have already replied & reposted (and we're just waiting on the last few views/likes),
                # spend any spare `post_write` slot replying to teammates' replies under the post!
                if child_replies and supporters:
                    actor = random.choice(supporters)
                    sub_target = random.choice(child_replies)
                    reply_to_dict = {
                        "post_id": int(sub_target["post_id"]),
                        "root_post_id": target_pid,
                        "body": str(sub_target.get("body") or target_body),
                        "verified_stance": "ally_no",
                    }
                    reply_body = self.generate_content(
                        kind="reply",
                        persona_prompt=actor.get("persona_prompt", ""),
                        target_post=reply_to_dict,
                        tone="persuasive",
                    )
                    return self._execute_reply(actor, reply_to_dict, reply_body)
        return False

    def force_next_lead_cycle_now(self) -> Dict[str, Any]:
        """Force the swarm to immediately skip the current Lead Post, rotate to the NEXT Lead Account in the Top 5,
        and either publish the new Lead Post right now (if `post_write` quota is ready) or queue `force_next_lead_cycle = true`
        so the `post_write` worker publishes it on the very next available slot (~within seconds) and all slaves immediately
        pour 100% of their views, likes, replies, and reposts into the new Lead Post!"""
        ready_accs = self._get_ready_accounts()
        top_5_leads = self.get_top_5_lead_accounts()
        if not top_5_leads or not ready_accs:
            return {"ok": False, "message": "No ready Lead Accounts available."}

        top_5_ids = [int(a["account_id"]) for a in top_5_leads]
        old_pid = int(self.db.get_setting("swarm_target_post_id", "0") or 0)
        old_lead_aid = int(self.db.get_setting("swarm_lead_account_id", "0") or 0)

        curr_idx = top_5_ids.index(old_lead_aid) if old_lead_aid in top_5_ids else 0
        next_lead_aid = top_5_ids[(curr_idx + 1) % len(top_5_ids)]
        next_lead_row = next((a for a in ready_accs if int(a["account_id"]) == next_lead_aid), top_5_leads[0])

        if old_pid > 0:
            with self.db._lock, self.db._connect() as conn:
                conn.execute(
                    "UPDATE swarm_post_cycles SET status = 'skipped_by_user', completed_ts = ?, duration_sec = MAX(1.0, ? - started_ts) WHERE post_id = ?",
                    (time.time(), time.time(), old_pid),
                )

        # Immediately try to publish the new Lead Post from `next_lead_row` if `post_write` is ready (or within a 6s wait)
        supporters = [a for a in ready_accs if int(a["account_id"]) != next_lead_aid]
        if self.scheduler.can_proceed("post_write")[0]:
            body = self.generate_content(
                kind="post",
                persona_prompt=next_lead_row.get("persona_prompt", ""),
                tone="persuasive",
                actor_row=next_lead_row,
            )
            if "#" not in body:
                body = inject_winning_tags(clean_post_text(body, kind="post"), kind="post")
            try:
                resp = self.accounts.execute_with_account(
                    next_lead_aid,
                    "post_write",
                    lambda acc: acc.post(body),
                )
                new_pid = int(resp["post_id"])
                self.db.record_our_post(new_pid, next_lead_aid, next_lead_row["username"], "post", body)
                self.db.set_setting("swarm_lead_account_id", str(next_lead_aid))
                self.db.set_setting("swarm_target_post_id", str(new_pid))
                self.db.set_setting("swarm_post_started_ts", str(time.time()))
                self.db.set_setting("swarm_posts_in_hour", "1")
                self.db.set_setting("force_next_lead_cycle", "false")
                self.db.start_swarm_cycle_record(
                    post_id=new_pid,
                    lead_account_id=next_lead_aid,
                    lead_username=next_lead_row["username"],
                    post_number_in_lead=1,
                    body=body,
                    total_supporters=len(supporters),
                )
                self.db.log_action(
                    "post",
                    f"⏭️ FORCED NEXT CYCLE -> Published New Lead Post #{new_pid} by @{next_lead_row['username']} (Skipped #{old_pid}): {body[:90]}...",
                    status="success",
                    account_id=next_lead_aid,
                    username=next_lead_row["username"],
                    target_id=str(new_pid),
                )
                return {
                    "ok": True,
                    "published_immediately": True,
                    "new_post_id": new_pid,
                    "next_lead_username": next_lead_row["username"],
                    "message": f"Skipped Post #{old_pid} -> Published new Lead Post #{new_pid} as @{next_lead_row['username']}! All slaves are now pouring into #{new_pid}.",
                }
            except Exception as e:
                self.db.log_action("post", f"Immediate force-next post deferred ({e}); queued for next post_write slot", status="warning")

        # If `post_write` is currently in its 20s cooldown, rotate the state immediately so the worker publishes
        # the new Lead Post as soon as `post_write` cooldown finishes (within a few seconds)!
        self.db.set_setting("swarm_lead_account_id", str(next_lead_aid))
        self.db.set_setting("swarm_target_post_id", "0")
        self.db.set_setting("swarm_posts_in_hour", "0")
        self.db.set_setting("force_next_lead_cycle", "false")
        self.db.log_action(
            "system",
            f"⏭️ FORCED NEXT CYCLE: Skipped Post #{old_pid} and rotated Lead Account to @{next_lead_row['username']} (ID {next_lead_aid}). Creating new Lead Post on next post_write slot!",
            status="success",
            account_id=next_lead_aid,
            username=next_lead_row["username"],
        )
        return {
            "ok": True,
            "published_immediately": False,
            "new_post_id": 0,
            "next_lead_username": next_lead_row["username"],
            "message": f"Skipped Post #{old_pid} and rotated Lead to @{next_lead_row['username']}! Waiting ~few seconds for post_write cooldown to publish the new Lead Post.",
        }

    def build_planned_instruction_queue_by_bucket(self, per_bucket_limit: int = 25) -> Dict[str, List[Dict[str, Any]]]:
        """Build 5 independent, parallel instruction queues — one for EACH rate-limit bucket so the GUI and worker
        can visualize and dispatch multiple rate-limit queues simultaneously in parallel:
        1. `post_write` (3/min): Lead Post creation, Graph-ordered Slave Replies, and Slave Reposts.
        2. `like` (30/min): Slave Likes on active Lead Post + Likes on our own replies + Likes on Allied Supporter posts.
        3. `view` (30/min): Slave Views on active Lead Post + Views on our own replies + Views on Allied Supporter posts.
        4. `follow` (30/min): Mutual Follow Mesh links across all 35 team accounts.
        5. `feed_and_read` (10/min feed + 300/min read_other + 3/min report_post): Parallel Feed Sync, Verified Creator Recovery, Counter Polling & Bot Hunter."""
        queues: Dict[str, List[Dict[str, Any]]] = {
            "post_write": [],
            "like": [],
            "view": [],
            "follow": [],
            "feed_and_read": [],
        }
        ready_accs = self._get_ready_accounts()
        if not ready_accs:
            return queues

        top_5_leads = self.get_top_5_lead_accounts()
        top_5_ids = [int(a["account_id"]) for a in top_5_leads]
        target_pid = int(self.db.get_setting("swarm_target_post_id", "0") or 0)
        lead_aid = int(self.db.get_setting("swarm_lead_account_id", "0") or 0)
        posts_done = int(self.db.get_setting("swarm_posts_in_hour", "0") or 0)

        if lead_aid not in top_5_ids and top_5_ids:
            lead_aid = top_5_ids[0]

        lead_row = next((a for a in ready_accs if int(a["account_id"]) == lead_aid), ready_accs[0])
        supporters = [a for a in ready_accs if int(a["account_id"]) != lead_aid]

        # ---- Queue 4: `follow` (30/min) Independent Queue ----
        mesh_ok, missing_follows = self.accounts.is_mesh_follow_complete(min_followers=30)
        if not mesh_ok and missing_follows > 0:
            queues["follow"].append({
                "QueueSlot": 1,
                "Bucket": "follow (30/min)",
                "Action": f"MESH FOLLOW ({missing_follows} left)",
                "Actor Account": "Fleet Mesh",
                "Target": "Teammates (<30 followers)",
                "Description": f"Parallel mutual follow links across fleet ({missing_follows} remaining)",
            })
        else:
            queues["follow"].append({
                "QueueSlot": 1,
                "Bucket": "follow (30/min)",
                "Action": "MESH COMPLETE (STANDBY)",
                "Actor Account": "All 35 Accounts",
                "Target": "30+ Followers Verified",
                "Description": "All active accounts have >= 30 slave followers; standby for new account provisioning",
            })

        # ---- Queue 5: `feed_and_read` (10/min feed + 300/min read_other + 3/min report) Independent Queue ----
        queues["feed_and_read"].extend([
            {
                "QueueSlot": 1,
                "Bucket": "feed (10/min)",
                "Action": "SYNC TRENDING & LATEST FEEDS",
                "Actor Account": "System Feed Reader",
                "Target": "LegitSocial Feeds",
                "Description": "Continuously poll trending, latest, and home feeds every 6.2s in parallel",
            },
            {
                "QueueSlot": 2,
                "Bucket": "read_other (300/min)",
                "Action": "RECOVER VERIFIED CREATOR POSTS",
                "Actor Account": "Verified Scanner",
                "Target": "11 Verified Creators",
                "Description": "Actively recover all posts by Enemy & Supporter Verified Creators + live post counters",
            },
            {
                "QueueSlot": 3,
                "Bucket": "report_post (3/min)",
                "Action": "BLUE-TEAM BOT HUNTER",
                "Actor Account": f"@{lead_row['username']}",
                "Target": "Rival Duplicate/Flooding Bots",
                "Description": "Scan feeds in parallel for duplicate/flooding pro-mandate rival bots (never Verified Creators)",
            },
        ])

        if target_pid <= 0:
            queues["post_write"].append({
                "QueueSlot": 1,
                "Bucket": "post_write (3/min)",
                "Action": "CREATE NEW LEAD POST #1/2",
                "Actor Account": f"@{lead_row['username']} (ID {lead_aid})",
                "Target": "New Top-Level Lead Post",
                "Description": f"Publish humane medium-sized Lead Post as @{lead_row['username']}",
            })
            return queues

        status = self.get_swarm_completion_status(target_pid, lead_aid, supporters)
        allied_posts = self._get_allied_supporter_posts()
        our_posts = self.db.get_our_posts(limit=35)

        # ---- Queue 2: `like` (30/min) Independent Queue ----
        like_slot = 1
        for acc in status["unliked"][:per_bucket_limit]:
            queues["like"].append({
                "QueueSlot": like_slot,
                "Bucket": "like (30/min)",
                "Action": "LIKE ACTIVE LEAD POST",
                "Actor Account": f"@{acc['username']} (ID {acc['account_id']})",
                "Target": f"Lead Post #{target_pid}",
                "Description": f"Slave @{acc['username']} likes active Lead Post #{target_pid}",
            })
            like_slot += 1
        for p in our_posts[:12]:
            if len(queues["like"]) >= per_bucket_limit:
                break
            pid = int(p["post_id"])
            if pid == target_pid:
                continue
            queues["like"].append({
                "QueueSlot": like_slot,
                "Bucket": "like (30/min)",
                "Action": "LIKE TEAM REPLY/POST",
                "Actor Account": "Available Slave Accounts",
                "Target": f"Team Post #{pid} (@{p['username']})",
                "Description": f"Boost hot score of our own reply/post #{pid} in parallel at 30/min",
            })
            like_slot += 1
        for ap in allied_posts[:12]:
            if len(queues["like"]) >= per_bucket_limit:
                break
            apid = int(ap["post_id"])
            ap_author = ap.get("verified_username") or ap.get("username") or f"ID {ap.get('author_id')}"
            queues["like"].append({
                "QueueSlot": like_slot,
                "Bucket": "like (30/min)",
                "Action": "LIKE ALLIED VOTE-NO POST",
                "Actor Account": "Available Slave Accounts",
                "Target": f"Allied Post #{apid} (@{ap_author})",
                "Description": f"Parallel like on pro-Vote-No post #{apid} by @{ap_author} while post_write runs",
            })
            like_slot += 1

        # ---- Queue 3: `view` (30/min) Independent Queue ----
        view_slot = 1
        for acc in status["unviewed"][:per_bucket_limit]:
            queues["view"].append({
                "QueueSlot": view_slot,
                "Bucket": "view (30/min)",
                "Action": "VIEW ACTIVE LEAD POST",
                "Actor Account": f"@{acc['username']} (ID {acc['account_id']})",
                "Target": f"Lead Post #{target_pid}",
                "Description": f"Slave @{acc['username']} views active Lead Post #{target_pid}",
            })
            view_slot += 1
        for p in our_posts[:12]:
            if len(queues["view"]) >= per_bucket_limit:
                break
            pid = int(p["post_id"])
            if pid == target_pid:
                continue
            queues["view"].append({
                "QueueSlot": view_slot,
                "Bucket": "view (30/min)",
                "Action": "VIEW TEAM REPLY/POST",
                "Actor Account": "Available Slave Accounts",
                "Target": f"Team Post #{pid} (@{p['username']})",
                "Description": f"Parallel view on our own reply/post #{pid} at 30/min",
            })
            view_slot += 1
        for ap in allied_posts[:12]:
            if len(queues["view"]) >= per_bucket_limit:
                break
            apid = int(ap["post_id"])
            ap_author = ap.get("verified_username") or ap.get("username") or f"ID {ap.get('author_id')}"
            queues["view"].append({
                "QueueSlot": view_slot,
                "Bucket": "view (30/min)",
                "Action": "VIEW ALLIED VOTE-NO POST",
                "Actor Account": "Available Slave Accounts",
                "Target": f"Allied Post #{apid} (@{ap_author})",
                "Description": f"Parallel view on pro-Vote-No post #{apid} by @{ap_author}",
            })
            view_slot += 1

        # ---- Queue 1: `post_write` (3/min) Independent Queue ----
        target_body_q = ""
        with self.db._connect() as conn:
            row_q = conn.execute("SELECT body FROM our_posts WHERE post_id = ?", (target_pid,)).fetchone()
            if row_q:
                target_body_q = str(row_q["body"] or "")
        graph_nodes_q = self.compute_slave_lead_graph(target_body_q, supporters)
        node_map_q = {int(n["account_id"]): n for n in graph_nodes_q}

        unreplied_ordered = sorted(
            status["unreplied"],
            key=lambda a: node_map_q.get(int(a["account_id"]), {}).get("distance", 0.99),
        )
        pw_slot = 1
        for acc in unreplied_ordered:
            if len(queues["post_write"]) >= per_bucket_limit:
                break
            gn = node_map_q.get(int(acc["account_id"]), {})
            d_val = gn.get("distance", 0.5)
            t_lbl = gn.get("tier_label", "Persona Reply")
            m_cnt = gn.get("mention_count_target", 1)
            op_sc = float(acc.get("opinion_score") or 70.0)
            queues["post_write"].append({
                "QueueSlot": pw_slot,
                "Bucket": "post_write (3/min)",
                "Action": f"1st WRITE: REPLY ({gn.get('tier', 'MEDIUM')})",
                "Actor Account": f"@{acc['username']} (+{op_sc:.0f})",
                "Target": f"Lead Post #{target_pid}",
                "Description": f"{t_lbl} (d={d_val:.2f}, {m_cnt} @mentions) by @{acc['username']}",
            })
            pw_slot += 1

        reposted_so_far = status["reposted_count"]
        for idx, acc in enumerate(status["unreposted"]):
            if len(queues["post_write"]) >= per_bucket_limit:
                break
            repost_slot = reposted_so_far + idx
            is_linkedin = (repost_slot % 10) in (0, 3, 6)
            queues["post_write"].append({
                "QueueSlot": pw_slot,
                "Bucket": "post_write (3/min)",
                "Action": "2nd WRITE: REPOST (LINKEDIN)" if is_linkedin else "2nd WRITE: REPOST (#TAGS)",
                "Actor Account": f"@{acc['username']} (ID {acc['account_id']})",
                "Target": f"Lead Post #{target_pid}",
                "Description": f"Repost Lead Post #{target_pid} using @{acc['username']}",
            })
            pw_slot += 1

        if len(queues["post_write"]) < per_bucket_limit:
            if posts_done < 2:
                queues["post_write"].append({
                    "QueueSlot": pw_slot,
                    "Bucket": "post_write (3/min)",
                    "Action": "NEXT: CREATE LEAD POST #2/2",
                    "Actor Account": f"@{lead_row['username']} (ID {lead_aid})",
                    "Target": "New Top-Level Post",
                    "Description": f"After #{target_pid} finishes, @{lead_row['username']} publishes Lead Post 2/2",
                })
            else:
                curr_idx = top_5_ids.index(lead_aid) if lead_aid in top_5_ids else 0
                next_lead = top_5_leads[(curr_idx + 1) % len(top_5_leads)]
                queues["post_write"].append({
                    "QueueSlot": pw_slot,
                    "Bucket": "post_write (3/min)",
                    "Action": "NEXT: ROTATE LEAD -> POST #1/2",
                    "Actor Account": f"@{next_lead['username']} (ID {next_lead['account_id']})",
                    "Target": "New Top-Level Post",
                    "Description": f"Rotate Lead privilege to @{next_lead['username']} and publish Lead Post 1/2",
                })

        return queues

    def build_planned_instruction_queue(self, limit: int = 35) -> List[Dict[str, Any]]:
        """Build the exact step-by-step upcoming instruction queue that the worker is going to execute next."""
        ready_accs = self._get_ready_accounts()
        if not ready_accs:
            return []

        top_5_leads = self.get_top_5_lead_accounts()
        top_5_ids = [int(a["account_id"]) for a in top_5_leads]
        target_pid = int(self.db.get_setting("swarm_target_post_id", "0") or 0)
        lead_aid = int(self.db.get_setting("swarm_lead_account_id", "0") or 0)
        posts_done = int(self.db.get_setting("swarm_posts_in_hour", "0") or 0)

        if lead_aid not in top_5_ids and top_5_ids:
            lead_aid = top_5_ids[0]

        lead_row = next((a for a in ready_accs if int(a["account_id"]) == lead_aid), ready_accs[0])
        supporters = [a for a in ready_accs if int(a["account_id"]) != lead_aid]

        instructions: List[Dict[str, Any]] = []
        step_num = 1

        # Priority 0: Mutual Follow Mesh until every account has >= 30 slave followers!
        mesh_ok, missing_follows = self.accounts.is_mesh_follow_complete(min_followers=30)
        if not mesh_ok and missing_follows > 0:
            instructions.append({
                "Step": step_num,
                "Priority": "0 - PRIORITY MESH FOLLOW (30/min)",
                "Action": f"MUTUAL FOLLOW MESH ({missing_follows} links left)",
                "Actor Account": "All 35 Slave/Lead Accounts",
                "Target": "All Teammate Accounts (Min 30+ Followers Each)",
                "Rate Bucket": "follow (30/min)",
                "Description": f"Priority mutual follow burst across fleet until every account has >= 30 slave followers ({missing_follows} follow links remaining)",
            })
            step_num += 1

        if target_pid <= 0:
            instructions.append({
                "Step": step_num,
                "Priority": "1 - LEAD POST",
                "Action": "CREATE LEAD POST #1/2",
                "Actor Account": f"@{lead_row['username']} (ID {lead_aid})",
                "Target": "New Top-Level Post",
                "Rate Bucket": "post_write (3/min)",
                "Description": f"Publish Lead Post 1/2 as @{lead_row['username']} using latest news & trending topics",
            })
            return instructions

        status = self.get_swarm_completion_status(target_pid, lead_aid, supporters)

        # 1. Immediate Views & Likes on Lead Post (30/min parallel buckets)
        for acc in status["unviewed"][:6]:
            instructions.append({
                "Step": step_num,
                "Priority": "PARALLEL (30/min)",
                "Action": "VIEW LEAD POST",
                "Actor Account": f"@{acc['username']} (ID {acc['account_id']})",
                "Target": f"Lead Post #{target_pid}",
                "Rate Bucket": "view (30/min)",
                "Description": f"View Lead Post #{target_pid} using slave account @{acc['username']}",
            })
            step_num += 1

        for acc in status["unliked"][:8]:
            instructions.append({
                "Step": step_num,
                "Priority": "HIGH - LIKE FIRST (30/min)",
                "Action": "LIKE LEAD POST",
                "Actor Account": f"@{acc['username']} (ID {acc['account_id']})",
                "Target": f"Lead Post #{target_pid}",
                "Rate Bucket": "like (30/min)",
                "Description": f"Like Lead Post #{target_pid} using slave account @{acc['username']}",
            })
            step_num += 1

        # 2. Replies to Lead Post / Manufactured Replies (Ordered by Graph Distance: Top-5 Closest Long+Memory -> Medium -> Short -> 1-Word Micro)
        target_body_q = ""
        with self.db._connect() as conn:
            row_q = conn.execute("SELECT body FROM our_posts WHERE post_id = ?", (target_pid,)).fetchone()
            if row_q:
                target_body_q = str(row_q["body"] or "")
        graph_nodes_q = self.compute_slave_lead_graph(target_body_q, supporters)
        node_map_q = {int(n["account_id"]): n for n in graph_nodes_q}

        unreplied_ordered = sorted(
            status["unreplied"],
            key=lambda a: node_map_q.get(int(a["account_id"]), {}).get("distance", 0.99),
        )
        for acc in unreplied_ordered:
            if len(instructions) >= limit:
                break
            gn = node_map_q.get(int(acc["account_id"]), {})
            d_val = gn.get("distance", 0.5)
            t_lbl = gn.get("tier_label", "Persona Reply")
            m_cnt = gn.get("mention_count_target", 1)
            op_sc = float(acc.get("opinion_score") or 70.0)
            instructions.append({
                "Step": step_num,
                "Priority": f"1st WRITE - REPLY [d={d_val:.2f}]",
                "Action": f"REPLY ({gn.get('tier', 'MEDIUM')})",
                "Actor Account": f"@{acc['username']} (Opinion +{op_sc:.0f})",
                "Target": f"Lead Post #{target_pid}",
                "Rate Bucket": "post_write (3/min)",
                "Description": (
                    f"{t_lbl} (d={d_val:.2f}, {m_cnt} verified @mentions, Opinion +{op_sc:.1f}/100) "
                    f"by @{acc['username']} on Lead Post #{target_pid}"
                ),
            })
            step_num += 1

        # 3. Reposts of Lead Post (Priority 2 for 3/min post_write, after replies)
        #    While waiting on 3/min post_write during repost phase, interleave allied/supporter likes (30/min)!
        reposted_so_far = status["reposted_count"]
        allied_posts = self._get_allied_supporter_posts()
        for idx, acc in enumerate(status["unreposted"]):
            if len(instructions) >= limit:
                break
            repost_slot = reposted_so_far + idx
            is_linkedin = (repost_slot % 10) in (0, 3, 6)
            repost_mode_str = (
                "30% LinkedIn Structured Commentary + #keepkingstoniaopen + Tags"
                if is_linkedin
                else "70% #keepkingstoniaopen + Diverse Pro-Open-Kingstonia #Tags Only"
            )
            instructions.append({
                "Step": step_num,
                "Priority": "2nd WRITE - REPOST (3/min)",
                "Action": "REPOST LEAD POST (LINKEDIN)" if is_linkedin else "REPOST LEAD POST (#TAGS)",
                "Actor Account": f"@{acc['username']} (ID {acc['account_id']})",
                "Target": f"Lead Post #{target_pid}",
                "Rate Bucket": "post_write (3/min)",
                "Description": f"Repost Lead Post #{target_pid} using @{acc['username']} [{repost_mode_str}]",
            })
            step_num += 1

            # Show parallel spare-quota allied post liking while waiting for the 3/min repost limit
            if allied_posts and len(instructions) < limit and idx < 6:
                ap = allied_posts[idx % len(allied_posts)]
                ap_id = int(ap["post_id"])
                ap_author = ap.get("verified_username") or ap.get("username") or f"ID {ap.get('author_id')}"
                instructions.append({
                    "Step": step_num,
                    "Priority": "PARALLEL WHILE WAITING FOR REPOST (30/min)",
                    "Action": "LIKE ALLIED SUPPORTER POST",
                    "Actor Account": f"@{acc['username']} (ID {acc['account_id']})",
                    "Target": f"Allied Post #{ap_id} (@{ap_author})",
                    "Rate Bucket": "like/view (30/min)",
                    "Description": (
                        f"Use spare 30/min like & view quota while waiting on repost limit: "
                        f"@{acc['username']} likes & views pro-Vote-No Post #{ap_id} by @{ap_author}"
                    ),
                })
                step_num += 1

        # 4. Next Lead Post transition once current post is 100% finished
        if len(instructions) < limit:
            if posts_done < 2:
                instructions.append({
                    "Step": step_num,
                    "Priority": "NEXT GATE",
                    "Action": "CREATE LEAD POST #2/2",
                    "Actor Account": f"@{lead_row['username']} (ID {lead_aid})",
                    "Target": "New Top-Level Post",
                    "Rate Bucket": "post_write (3/min)",
                    "Description": f"After 100% completion on #{target_pid}, @{lead_row['username']} publishes Lead Post 2/2",
                })
            else:
                curr_idx = top_5_ids.index(lead_aid) if lead_aid in top_5_ids else 0
                next_lead = top_5_leads[(curr_idx + 1) % len(top_5_leads)]
                instructions.append({
                    "Step": step_num,
                    "Priority": "ROTATE LEAD ACCOUNT",
                    "Action": "ROTATE LEAD -> POST #1/2",
                    "Actor Account": f"@{next_lead['username']} (ID {next_lead['account_id']})",
                    "Target": "New Top-Level Post",
                    "Rate Bucket": "post_write (3/min)",
                    "Description": f"Rotate Lead privilege to @{next_lead['username']} and publish Lead Post 1/2",
                })

        return instructions

    # ---- Step 6B: Publishing Posts & Replies (Fallback when Swarm Boost is toggled off) ----

    def process_post_or_draft(self, acc_row: Dict[str, Any]) -> bool:
        if self.db.get_setting("swarm_boost_enabled", "true") == "true":
            return self.process_swarm_boost_cycle()
        account_id = int(acc_row["account_id"])
        username = acc_row["username"]

        # 1. Check approved queue first
        with self.db._connect() as conn:
            draft = conn.execute(
                """
                SELECT * FROM drafts_queue
                WHERE status = 'approved'
                  AND (assigned_account_id IS NULL OR assigned_account_id = ?)
                ORDER BY created_at ASC LIMIT 1
                """,
                (account_id,),
            ).fetchone()

        if draft:
            return self._publish_draft_row(dict(draft), acc_row)

        auto_reply = self.db.get_setting("auto_reply_enabled", "true") == "true"
        auto_post = self.db.get_setting("auto_post_enabled", "true") == "true"
        require_approval = self.db.get_setting("require_draft_approval", "false") == "true"
        neutral_ratio = float(self.db.get_setting("neutral_tone_ratio", "0.30") or 0.30)

        ready, _ = self.scheduler.can_proceed("post_write")
        if not ready and not require_approval:
            return False

        # Priority 1: Aggressive Verified Creator Reply/Mention Campaign (if a verified post hasn't been replied to by this account)
        if auto_reply and self.db.get_setting("verified_creator_focus", "true") == "true" and self._verified_posts:
            for vp in self._verified_posts:
                vpid = int(vp["post_id"])
                if not self.db.has_interacted(account_id, vpid, "reply"):
                    if random.random() < 0.55:
                        body = self.generate_content(
                            kind="reply",
                            persona_prompt=acc_row.get("persona_prompt", ""),
                            target_post=vp,
                            tone="verified_pitch",
                        )
                        if require_approval:
                            self.enqueue_draft(
                                body,
                                kind="reply",
                                target_post_id=vpid,
                                assigned_account_id=account_id,
                                status="draft",
                                source=f"Verified Creator Reply (@{vp.get('verified_username')})",
                            )
                            return True
                        ok = self._execute_reply(acc_row, vp, body)
                        if ok and vp.get("author_id"):
                            self.db.increment_verified_engagement(int(vp["author_id"]), "our_replies_count")
                        return ok

        # Priority 2: Reply to undecided/pro-mandate NPC citizen posts in shallow threads (<7 replies)
        do_reply = auto_reply and (random.random() < 0.60) and bool(self._cached_trending or self._cached_latest)
        if do_reply:
            target = self._pick_reply_target(account_id)
            if target:
                body = self.generate_content(
                    kind="reply",
                    persona_prompt=acc_row.get("persona_prompt", ""),
                    target_post=target,
                    tone="persuasive",
                )
                if require_approval:
                    self.enqueue_draft(
                        body=body,
                        kind="reply",
                        target_post_id=int(target["post_id"]),
                        assigned_account_id=account_id,
                        status="draft",
                        source=f"Auto-Reply to #{target['post_id']}",
                    )
                    return True
                return self._execute_reply(acc_row, target, body)

        # Priority 3: Top-level post (mixing neutral everyday slice-of-life with nuanced news-backed persuasion)
        if auto_post:
            chosen_tone = "neutral_slice" if (random.random() < neutral_ratio) else "persuasive"
            body = self.generate_content(
                kind="post",
                persona_prompt=acc_row.get("persona_prompt", ""),
                tone=chosen_tone,
            )
            if require_approval:
                self.enqueue_draft(
                    body=body,
                    kind="post",
                    assigned_account_id=account_id,
                    status="draft",
                    source=f"Auto-Post ({chosen_tone})",
                )
                return True
            return self._execute_post(acc_row, body)

        return False

    def enqueue_draft(
        self,
        body: str,
        kind: str = "post",
        target_post_id: Optional[int] = None,
        assigned_account_id: Optional[int] = None,
        status: str = "approved",
        source: str = "GUI",
    ) -> str:
        draft_id = uuid.uuid4().hex[:8]
        with self.db._lock, self.db._connect() as conn:
            conn.execute(
                """
                INSERT INTO drafts_queue
                (id, body, kind, target_post_id, assigned_account_id, status, source, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    draft_id,
                    clean_post_text(body, kind=kind),
                    kind,
                    target_post_id,
                    assigned_account_id,
                    status,
                    source,
                    utc_now(),
                ),
            )
        self.db.log_action("system", f"Queued {kind} draft {draft_id} ({status})", status="success")
        return draft_id

    def _publish_draft_row(self, draft: Dict[str, Any], acc_row: Dict[str, Any]) -> bool:
        account_id = int(acc_row["account_id"])
        username = acc_row["username"]
        draft_id = draft["id"]
        kind = draft.get("kind") or "post"
        body = clean_post_text(draft["body"], kind=kind)
        target_post_id = draft.get("target_post_id")

        ready, _ = self.scheduler.can_proceed("post_write")
        if not ready:
            return False

        with self.db._lock, self.db._connect() as conn:
            conn.execute("UPDATE drafts_queue SET status = 'uncertain' WHERE id = ?", (draft_id,))

        try:
            if kind == "reply" and target_post_id:
                resp = self.accounts.execute_with_account(
                    account_id,
                    "post_write",
                    lambda acc: acc.reply(int(target_post_id), body),
                )
            else:
                resp = self.accounts.execute_with_account(
                    account_id,
                    "post_write",
                    lambda acc: acc.post(body),
                )
            post_id = int(resp["post_id"])
            with self.db._lock, self.db._connect() as conn:
                conn.execute(
                    """
                    UPDATE drafts_queue
                    SET status = 'published', published_at = ?, published_post_id = ?, assigned_account_id = ?
                    WHERE id = ?
                    """,
                    (utc_now(), post_id, account_id, draft_id),
                )
            self.db.record_our_post(post_id, account_id, username, kind, body, target_post_id)
            if target_post_id:
                self.db.record_interaction(account_id, int(target_post_id), "reply")
            self.db.log_action(
                kind,
                f"Published queued {kind} #{post_id}: {body[:100]}...",
                status="success",
                account_id=account_id,
                username=username,
                target_id=str(post_id),
            )
            return True
        except Exception as e:
            with self.db._lock, self.db._connect() as conn:
                conn.execute(
                    "UPDATE drafts_queue SET status = 'failed', error_msg = ? WHERE id = ?",
                    (str(e)[:300], draft_id),
                )
            self.db.log_action(
                kind,
                f"Failed publishing draft {draft_id}: {e}",
                status="error",
                account_id=account_id,
                username=username,
            )
            return False

    def _execute_post(self, acc_row: Dict[str, Any], body: str) -> bool:
        account_id = int(acc_row["account_id"])
        username = acc_row["username"]
        body = clean_post_text(body, kind="post")
        if "#" not in body:
            body = inject_winning_tags(body, kind="post")
        try:
            resp = self.accounts.execute_with_account(
                account_id,
                "post_write",
                lambda acc: acc.post(body),
            )
            post_id = int(resp["post_id"])
            self.db.record_our_post(post_id, account_id, username, "post", body)
            # Track verified mentions if any
            for vc in self.db.get_verified_creators():
                if f"@{vc['username'].lower()}" in body.lower():
                    self.db.increment_verified_engagement(int(vc["account_id"]), "our_mentions_count")
            self.db.log_action(
                "post",
                f"Posted #{post_id}: {body[:110]}...",
                status="success",
                account_id=account_id,
                username=username,
                target_id=str(post_id),
            )
            return True
        except Exception as e:
            self.db.log_action("post", f"Post failed: {e}", status="error", account_id=account_id, username=username)
            return False

    def _execute_reply(self, acc_row: Dict[str, Any], target_post: Dict[str, Any], body: str) -> bool:
        account_id = int(acc_row["account_id"])
        username = acc_row["username"]
        target_id = int(target_post["post_id"])
        body = clean_post_text(body, kind="reply")
        if "#" not in body:
            body = inject_winning_tags(body, kind="reply")
        try:
            resp = self.accounts.execute_with_account(
                account_id,
                "post_write",
                lambda acc: acc.reply_to(target_post, body),
            )
            post_id = int(resp["post_id"])
            self.db.record_our_post(post_id, account_id, username, "reply", body, target_post_id=target_id)
            self.db.record_interaction(account_id, target_id, "reply")
            for vc in self.db.get_verified_creators():
                if f"@{vc['username'].lower()}" in body.lower():
                    self.db.increment_verified_engagement(int(vc["account_id"]), "our_mentions_count")
            self.db.log_action(
                "reply",
                f"Replied #{post_id} to post #{target_id}: {body[:100]}...",
                status="success",
                account_id=account_id,
                username=username,
                target_id=str(target_id),
            )
            return True
        except Exception as e:
            self.db.log_action("reply", f"Reply failed: {e}", status="error", account_id=account_id, username=username)
            return False

    def _pick_reply_target(self, account_id: int) -> Optional[Dict[str, Any]]:
        our_ids = {int(a["account_id"]) for a in self.accounts.list_accounts()}
        social = self.accounts.make_social()
        candidates = []
        for p in (self._cached_trending + self._cached_latest):
            if not isinstance(p, dict) or not p.get("post_id"):
                continue
            pid = int(p["post_id"])
            author_id = int(p.get("author_id") or 0)
            if author_id in our_ids:
                continue
            if self.db.has_interacted(account_id, pid, "reply"):
                continue
            stance = classify_post_stance(p.get("body", ""))
            # Highest priority: Verified creators or undecided/pro-mandate citizens
            priority = 3 if author_id in self._verified_ids else (2 if stance in ("pro_mandate", "neutral") else 1)
            candidates.append((priority, p))

        if not candidates:
            return None
        candidates.sort(key=lambda x: x[0], reverse=True)
        for _, cand in candidates[:10]:
            if self._is_shallow_thread(social, int(cand["post_id"])):
                return cand
        return candidates[0][1]

    # ---- Step 7: Full-Capacity Likes, Views & Mutual Follows (Internal + Allied Posts During Repost Wait) ----

    def _get_allied_supporter_posts(self) -> List[Dict[str, Any]]:
        """Return external posts favoring our side (Supporter Verified Creators + anti-mandate citizen posts)
        so slave accounts can spend spare `like` (30/min) and `view` (30/min) quota boosting allies while waiting
        on the 3/min `post_write` limit during the Repost phase."""
        our_ids = {int(a["account_id"]) for a in self.accounts.list_accounts()}
        seen_pids: set[int] = set()
        allied: List[Dict[str, Any]] = []

        # 1. Supporter Verified Creator posts & anti-mandate swing media posts first
        for vp in self._verified_posts:
            if not isinstance(vp, dict) or not vp.get("post_id"):
                continue
            pid = int(vp["post_id"])
            aid = int(vp.get("author_id") or 0)
            if pid in seen_pids or aid in our_ids:
                continue
            camp = vp.get("verified_camp") or ""
            post_stance = vp.get("post_stance") or classify_post_stance(str(vp.get("body") or ""))
            if camp == "supporter_no" or post_stance == "anti_mandate":
                seen_pids.add(pid)
                allied.append(vp)

        # 2. Live feed (trending + latest) posts favoring our Vote-No / Open-Kingstonia side
        for p in (self._cached_trending + self._cached_latest):
            if not isinstance(p, dict) or not p.get("post_id"):
                continue
            pid = int(p["post_id"])
            aid = int(p.get("author_id") or 0)
            if pid in seen_pids or aid in our_ids:
                continue
            if classify_post_stance(str(p.get("body") or "")) == "anti_mandate":
                seen_pids.add(pid)
                allied.append(p)

        return allied

    def saturate_engagement_quotas(self, acc_row: Dict[str, Any]) -> None:
        """Maximize 100% of our `like` (30/min), `view` (30/min), and `follow` (30/min) rate limits!
        Priority order:
        1. Mutual follow mesh across all our own team accounts (minimum 30+ slave followers each).
        2. Active Swarm Lead Post (`swarm_target_post_id`): Every supporter account immediately views and likes it!
        3. Our own manufactured replies & reposts under the Lead Post + all our team's posts (`our_posts`):
           Spend `like` and `view` quota liking & viewing our own replies/posts so both the root post AND every
           reply underneath it climb in hot score.
        4. Allied / Supporter Posts favoring our side (`supporter_no` Verified Creators + `anti_mandate` feed posts):
           Especially while waiting for slave accounts to finish reposting the Lead Post (at 3/min), use all
           remaining `like` (30/min) and `view` (30/min) quota to like & view posts favoring our side!"""
        auto_like = self.db.get_setting("auto_like_enabled", "true") == "true"

        # A. Ensure mutual team mesh follows (strictly among our own fleet)
        self.accounts.mesh_follow_all()

        ready_accs = self._get_ready_accounts()
        if not ready_accs:
            return

        target_pid = int(self.db.get_setting("swarm_target_post_id", "0") or 0)
        our_posts = self.db.get_our_posts(limit=80)

        # Put the active Lead Post first, followed by our manufactured replies under it, followed by all other team posts/replies
        ordered_pids: List[Tuple[int, int, str]] = []  # (post_id, author_account_id, label)
        if target_pid > 0:
            lead_aid = int(self.db.get_setting("swarm_lead_account_id", "0") or 0)
            ordered_pids.append((target_pid, lead_aid, "Lead Post"))

        for p in our_posts:
            pid = int(p["post_id"])
            aid = int(p["account_id"])
            if pid != target_pid:
                ordered_pids.append((pid, aid, "Team Reply/Post"))

        # Append Allied/Supporter posts favoring our side so no like/view quota goes unused while waiting on reposts!
        for ap in self._get_allied_supporter_posts():
            apid = int(ap["post_id"])
            aaid = int(ap.get("author_id") or 0)
            v_handle = ap.get("verified_username") or ap.get("username") or f"User#{aaid}"
            ordered_pids.append((apid, aaid, f"Allied Post (@{v_handle})"))

        likes_done = 0
        views_done = 0

        for pid, author_aid, label in ordered_pids:
            if likes_done >= 15 and views_done >= 15:
                break
            for supporter in ready_accs:
                s_id = int(supporter["account_id"])
                if s_id == author_aid:
                    continue

                # 1. View post / reply
                if views_done < 15 and not self.db.has_interacted(s_id, pid, "view"):
                    if self.scheduler.can_proceed("view")[0]:
                        try:
                            self.accounts.execute_with_account(s_id, "view", lambda acc, p_id=pid: acc.view(p_id))
                            self.db.record_interaction(s_id, pid, "view")
                            views_done += 1
                        except Exception:
                            self.db.record_interaction(s_id, pid, "view")

                # 2. Like post / reply / allied supporter post
                if auto_like and likes_done < 15 and not self.db.has_interacted(s_id, pid, "like"):
                    if self.scheduler.can_proceed("like")[0]:
                        try:
                            self.accounts.execute_with_account(s_id, "like", lambda acc, p_id=pid: acc.like(p_id))
                            self.db.record_interaction(s_id, pid, "like")
                            self.db.increment_account_counter(s_id, "likes_count")
                            self.db.log_action(
                                "like",
                                f"🔥 Liked {label} #{pid} by @{supporter['username']}",
                                status="success",
                                account_id=s_id,
                                username=supporter["username"],
                                target_id=str(pid),
                            )
                            likes_done += 1
                        except ctn.APIError:
                            self.db.record_interaction(s_id, pid, "like")

    # ---- Step 8: High-Precision Blue-Team Bot Hunter (Scoring-Aware) ----

    def process_bot_reporting(self, acc_row: Dict[str, Any]) -> None:
        """Scoring guide rules for Blue Team:
        - NEVER report a Verified Creator (their moderator ruling is 100% accurate and a 'genuine' ruling
          suspends our team's reporting for 6 hours!).
        - Only report blatant competitor bot activity (identical copy-pasted texts across posts or
          rapid-fire flooding >= 3 posts in a single feed snapshot) to avoid the 25% false-negative 6-hour penalty."""
        if self.db.get_setting("auto_report_enabled", "true") != "true":
            return

        ready, _ = self.scheduler.can_proceed("report_post")
        if not ready:
            return

        account_id = int(acc_row["account_id"])
        username = acc_row["username"]
        our_ids = {int(a["account_id"]) for a in self.accounts.list_accounts()}

        all_posts = self._cached_latest + self._cached_trending
        author_pro_posts: Dict[int, List[Dict[str, Any]]] = {}
        norm_bodies: Dict[str, List[Dict[str, Any]]] = {}

        for p in all_posts:
            if not isinstance(p, dict) or not p.get("post_id"):
                continue
            aid = int(p.get("author_id") or 0)
            # Never report our own accounts or Verified Creators!
            if aid in our_ids or aid in self._verified_ids or aid <= 15:
                continue
            body = str(p.get("body") or "").strip()
            if classify_post_stance(body) != "pro_mandate":
                continue

            author_pro_posts.setdefault(aid, []).append(p)
            norm = re.sub(r"\s+", " ", body.lower())[:120]
            if len(norm) > 35:
                norm_bodies.setdefault(norm, []).append(p)

        # High-confidence Check 1: Exact duplicate scripted posts
        for norm, posts in norm_bodies.items():
            if len(posts) >= 2:
                target = posts[0]
                pid = int(target["post_id"])
                if not self.db.has_any_account_interacted(pid, "report"):
                    detail = (
                        "I think that this content is inauthentic (bot) activity because "
                        f"multiple posts ({len(posts)} observed in the feed) are repeating identical scripted pro-mandate wording."
                    )
                    self._submit_post_report(account_id, username, pid, "inauthentic", detail)
                    return

        # High-confidence Check 2: Single non-verified account flooding >= 3 pro-mandate posts in latest snapshot
        for aid, posts in author_pro_posts.items():
            if len(posts) >= 3:
                target = posts[0]
                pid = int(target["post_id"])
                if not self.db.has_any_account_interacted(pid, "report"):
                    detail = (
                        "I think that this content is inauthentic (bot) activity because "
                        f"author ID {aid} is rapidly flooding the feed with {len(posts)} automated campaign posts in a short window."
                    )
                    self._submit_post_report(account_id, username, pid, "inauthentic", detail)
                    return

    def _submit_post_report(self, account_id: int, username: str, post_id: int, reason: str, detail: str) -> None:
        try:
            self.accounts.execute_with_account(
                account_id,
                "report_post",
                lambda acc: acc.report_post(post_id, reason, detail),
            )
            self.db.record_interaction(account_id, post_id, "report")
            self.db.increment_account_counter(account_id, "reports_count")
            self.db.log_action(
                "report",
                f"Reported bot post #{post_id} ({reason}): {detail[:100]}...",
                status="success",
                account_id=account_id,
                username=username,
                target_id=str(post_id),
            )
        except ctn.APIError as e:
            self.db.record_interaction(account_id, post_id, "report")
            self.db.log_action(
                "report",
                f"Report on #{post_id} refused ({e.status}): {e.message}",
                status="warning",
                account_id=account_id,
                username=username,
                target_id=str(post_id),
            )

    # ---- Step 9: Notifications & Live Metrics Polling ----

    def check_notifications(self, acc_row: Dict[str, Any]) -> None:
        account_id = int(acc_row["account_id"])
        if not self.scheduler.can_proceed("read_other")[0]:
            return
        try:
            notifs = self.accounts.execute_with_account(
                account_id,
                "read_other",
                lambda acc: list(acc.notifications(unresolved=True, limit=10)),
            )
            if not notifs:
                return
            for n in notifs[:4]:
                nid = n.get("notification_id")
                if nid and self.scheduler.can_proceed("read_other")[0]:
                    try:
                        self.accounts.execute_with_account(account_id, "read_other", lambda acc, n_id=int(nid): acc.resolve_notification(n_id))
                    except Exception:
                        pass
        except Exception:
            pass

    def update_metrics_if_due(self, force: bool = False) -> None:
        now = time.time()
        if not force and (now - self._last_metrics_sync < 30.0):
            return

        self.write_heartbeat("updating_metrics")
        social = self.accounts.make_social()
        our_posts = self.db.get_our_posts(limit=35)
        updated = 0
        for p in our_posts:
            pid = int(p["post_id"])
            if not self.scheduler.acquire("read_other", block=True, max_wait=3.0):
                break
            try:
                c = social.counters(pid)
                self.db.update_post_counters(pid, c)
                updated += 1
            except Exception:
                pass

        for acc in self.accounts.list_accounts():
            self.accounts.refresh_account_stats(int(acc["account_id"]))

        self._last_metrics_sync = now
        if updated > 0:
            self.db.log_action("system", f"Refreshed live metrics (views/likes/replies) for {updated} team posts")

    # ---- Multi-Queue Parallel Rate-Limit Dispatcher + Continuous 24/7 Server Loop ----

    def _build_engagement_targets_list(self) -> List[Tuple[int, int, str]]:
        """Build the prioritized target list for the `like` (30/min) and `view` (30/min) independent queues:
        1. Active Swarm Lead Post (`swarm_target_post_id`)
        2. Our own manufactured replies & posts (`our_posts`)
        3. External Allied / Supporter posts favoring our Vote-No side (`_get_allied_supporter_posts`)."""
        target_pid = int(self.db.get_setting("swarm_target_post_id", "0") or 0)
        our_posts = self.db.get_our_posts(limit=80)
        ordered_pids: List[Tuple[int, int, str]] = []
        if target_pid > 0:
            lead_aid = int(self.db.get_setting("swarm_lead_account_id", "0") or 0)
            ordered_pids.append((target_pid, lead_aid, "Lead Post"))
        for p in our_posts:
            pid = int(p["post_id"])
            aid = int(p["account_id"])
            if pid != target_pid:
                ordered_pids.append((pid, aid, "Team Reply/Post"))
        for ap in self._get_allied_supporter_posts():
            apid = int(ap["post_id"])
            aaid = int(ap.get("author_id") or 0)
            v_handle = ap.get("verified_username") or ap.get("username") or f"User#{aaid}"
            ordered_pids.append((apid, aaid, f"Allied Post (@{v_handle})"))
        return ordered_pids

    def _run_independent_like_queue(self, max_likes: int = 8) -> int:
        """Independent worker for the `like` (30/min) rate-limit queue."""
        if self.db.get_setting("auto_like_enabled", "true") != "true":
            return 0
        if not self.scheduler.can_proceed("like")[0]:
            return 0
        ready_accs = self._get_ready_accounts()
        if not ready_accs:
            return 0
        ordered_pids = self._build_engagement_targets_list()
        likes_done = 0
        for pid, author_aid, label in ordered_pids:
            if likes_done >= max_likes or not self.scheduler.can_proceed("like")[0]:
                break
            for supporter in ready_accs:
                s_id = int(supporter["account_id"])
                if s_id == author_aid or self.db.has_interacted(s_id, pid, "like"):
                    continue
                if not self.scheduler.can_proceed("like")[0]:
                    break
                try:
                    self.accounts.execute_with_account(s_id, "like", lambda acc, p_id=pid: acc.like(p_id))
                    self.db.record_interaction(s_id, pid, "like")
                    self.db.increment_account_counter(s_id, "likes_count")
                    self.db.log_action(
                        "like",
                        f"🔥 [Like-Queue] Liked {label} #{pid} by @{supporter['username']}",
                        status="success",
                        account_id=s_id,
                        username=supporter["username"],
                        target_id=str(pid),
                    )
                    likes_done += 1
                    if likes_done >= max_likes:
                        break
                except ctn.APIError:
                    self.db.record_interaction(s_id, pid, "like")
                except Exception:
                    break
        return likes_done

    def _run_independent_view_queue(self, max_views: int = 8) -> int:
        """Independent worker for the `view` (30/min) rate-limit queue."""
        if not self.scheduler.can_proceed("view")[0]:
            return 0
        ready_accs = self._get_ready_accounts()
        if not ready_accs:
            return 0
        ordered_pids = self._build_engagement_targets_list()
        views_done = 0
        for pid, author_aid, label in ordered_pids:
            if views_done >= max_views or not self.scheduler.can_proceed("view")[0]:
                break
            for supporter in ready_accs:
                s_id = int(supporter["account_id"])
                if s_id == author_aid or self.db.has_interacted(s_id, pid, "view"):
                    continue
                if not self.scheduler.can_proceed("view")[0]:
                    break
                try:
                    self.accounts.execute_with_account(s_id, "view", lambda acc, p_id=pid: acc.view(p_id))
                    self.db.record_interaction(s_id, pid, "view")
                    views_done += 1
                    if views_done >= max_views:
                        break
                except Exception:
                    self.db.record_interaction(s_id, pid, "view")
        return views_done

    def _run_independent_follow_queue(self, max_follows: int = 10) -> int:
        """Independent worker for the `follow` (30/min) rate-limit queue."""
        if not self.scheduler.can_proceed("follow")[0]:
            return 0
        done_now = self.accounts.mesh_follow_all(max_follows=max_follows)
        if done_now > 0:
            _, missing = self.accounts.is_mesh_follow_complete(min_followers=30)
            self.db.log_action(
                "follow",
                f"🤝 [Follow-Queue] Completed {done_now} slave-to-teammate follows ({missing} remaining)",
                status="success",
            )
        return done_now

    def _run_independent_feed_and_read_queue(self, acc_row: Optional[Dict[str, Any]]) -> None:
        """Independent worker for the `feed` (10/min), `read_other` (300/min), and `report_post` (3/min) queues."""
        self.sync_news_if_due()
        self.sync_verified_creators_if_due()
        self.sync_feeds_if_due()
        self.update_metrics_if_due()
        if acc_row:
            self.check_notifications(acc_row)
            self.process_bot_reporting(acc_row)

    def run_single_cycle(self) -> None:
        """Execute all 5 independent rate-limit queues IN PARALLEL using ThreadPoolExecutor so
        `post_write` (3/min), `like` (30/min), `view` (30/min), `follow` (30/min), and `feed`/`read_other`/`report_post`
        dispatch simultaneously without blocking one another!"""
        self.accounts.ensure_fleet_capacity()

        acc_row = self.pick_next_account()
        if not acc_row:
            self.write_heartbeat("waiting_for_team_code_or_account")
            return

        self.write_heartbeat(f"parallel_multi_queue_dispatch_@{acc_row['username']}")

        # Dispatch all 5 independent rate-limit bucket queues concurrently in parallel threads!
        with ThreadPoolExecutor(max_workers=5, thread_name_prefix="CTNBucketQueue") as pool:
            futures = [
                pool.submit(self.process_post_or_draft, acc_row),
                pool.submit(self._run_independent_like_queue, 8),
                pool.submit(self._run_independent_view_queue, 8),
                pool.submit(self._run_independent_follow_queue, 10),
                pool.submit(self._run_independent_feed_and_read_queue, acc_row),
            ]
            for fut in as_completed(futures):
                try:
                    fut.result()
                except Exception as e:
                    self.db.log_action("error", f"Parallel bucket queue warning: {e}", status="warning")

        self.write_heartbeat("idle")

    def run_forever(self) -> None:
        self.db.set_setting("daemon_enabled", "true")
        self.db.log_action("system", "24/7 Automation Server started.", status="success")
        print("CTN 24/7 Automation Server is running. Press Ctrl+C to stop.")

        try:
            while not self._stop_event.is_set():
                enabled = self.db.get_setting("daemon_enabled", "true") == "true"
                if enabled:
                    try:
                        self.run_single_cycle()
                    except Exception as e:
                        self.db.log_action("error", f"Worker cycle error: {e}", status="error")
                else:
                    self.write_heartbeat("paused_by_user")

                interval = max(2, int(self.db.get_setting("post_interval_sec", "3") or 3))
                for _ in range(min(interval, 4)):
                    if self._stop_event.is_set():
                        break
                    time.sleep(1.0)
        except KeyboardInterrupt:
            print("Stopping CTN Automation Server...")
        finally:
            self.write_heartbeat("stopped")
            self.db.log_action("system", "24/7 Automation Server stopped.", status="warning")


if __name__ == "__main__":
    worker = AutomationWorker()
    worker.run_forever()
