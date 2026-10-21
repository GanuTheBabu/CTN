import json
from datetime import datetime, timezone
from pathlib import Path

import capturethenarrative as ctn

# This just gets the trending posts and saves them. No login needed.
# I'm keeping the old files too so we can compare things later.
folder = Path(__file__).resolve().parent / "data"
history_folder = folder / "history"
folder.mkdir(exist_ok=True)
history_folder.mkdir(exist_ok=True)

print("Getting trending posts from CTN...")

try:
    social = ctn.LegitSocial(timeout=20)
    posts = list(social.feed_trending(limit=20))

    collected = datetime.now(timezone.utc)
    data = {
        "timestamp": collected.isoformat(),
        "posts": posts
    }

    # dashboard.py uses this file, so it gets replaced each time
    latest_file = folder / "trending.json"
    with open(latest_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    # Keep a copy of each run in history as well
    filename = collected.strftime("%Y%m%d_%H%M%S_%f") + ".json"
    old_copy = history_folder / filename
    with open(old_copy, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"Got {len(posts)} posts")
    print("Saved latest posts and a history copy.")

    # Just print a few so the terminal doesn't get too messy
    for post in posts[:5]:
        print("-", post.get("post_id"), post.get("body", "")[:130])

    if social.rate_limit:
        print("API requests left:", social.rate_limit.remaining)

except ctn.APIError as e:
    print("CTN API error:", e)
except OSError as e:
    print("File or connection error:", e)