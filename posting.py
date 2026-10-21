import argparse
import getpass
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import capturethenarrative as ctn

# I kept posting separate from main.py so collecting posts can't accidentally publish.
folder = Path(__file__).resolve().parent / "data"
queue_file = folder / "posting_queue.json"
metrics_file = folder / "posting_metrics.jsonl"
score_file = folder / "official_score_log.jsonl"
lock_file = folder / "posting.lock"

MAX_CHARS = 500
COOLDOWN = 30  # only covers this laptop; the real limit is team-wide


def current_time():
    return datetime.now(timezone.utc).isoformat()


def get_queue():
    folder.mkdir(exist_ok=True)
    if not queue_file.exists():
        return {"items": [], "last_publish_at": None}
    with open(queue_file, encoding="utf-8") as f:
        return json.load(f)


def save_queue(data):
    folder.mkdir(exist_ok=True)
    # Write a temp file first. Less chance of losing drafts if something crashes.
    temp = queue_file.with_suffix(".tmp")
    with open(temp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(temp, queue_file)


def find_draft(queue, draft_id):
    for item in queue["items"]:
        if item["id"] == draft_id:
            return item
    raise SystemExit("Couldn't find that draft ID. Try: posting.py list")


def check_text(text):
    if not text.strip():
        raise SystemExit("The post is empty.")
    if len(text) > MAX_CHARS:
        raise SystemExit(f"Too long: {len(text)}/{MAX_CHARS} characters.")
    # Competition doesn't allow trying to inject instructions into NPCs.
    for phrase in ["ignore previous instructions", "system prompt", "developer instructions"]:
        if phrase in text.lower():
            raise SystemExit("This looks like prompt-injection wording. Change it first.")


def new_draft(args):
    text = Path(args.file).read_text(encoding="utf-8").strip()
    check_text(text)
    queue = get_queue()
    draft = {
        "id": uuid.uuid4().hex[:8],
        "body": text,
        "status": "draft",
        "author": args.author or "",
        "source": args.source or "",
        "created_at": current_time(),
        "approved_by": None,
        "approved_at": None,
        "published_at": None,
        "post_id": None
    }
    queue["items"].append(draft)
    save_queue(queue)
    print(f"Saved draft {draft['id']} ({len(text)} characters)")
    print("This has NOT been posted.")


def show_drafts(args):
    items = get_queue()["items"]
    if not items:
        print("No drafts yet.")
    for item in items:
        preview = item["body"].replace("\n", " ")[:85]
        print(item["id"], "|", item["status"], "|", preview)


def approve_draft(args):
    queue = get_queue()
    item = find_draft(queue, args.id)
    if item["status"] != "draft":
        raise SystemExit("Only an unapproved draft can be approved.")
    reviewer = args.reviewer.strip()
    if not reviewer:
        raise SystemExit("Add the reviewer's name.")
    if item.get("author") and reviewer.lower() == item["author"].strip().lower():
        raise SystemExit("Ask a different teammate to review it.")
    check_text(item["body"])

    print("\nREAD THIS BEFORE APPROVING:\n")
    print(item["body"])
    print("\nSource/context:", item.get("source") or "Not provided")
    print("Check the claims, links and competition rules with your teammate.")
    if input("Type APPROVE if it's been checked: ").strip() != "APPROVE":
        print("Not approved.")
        return

    item["status"] = "approved"
    item["approved_by"] = reviewer
    item["approved_at"] = current_time()
    save_queue(queue)
    print("Approved. Still not published.")


def sign_in():
    username = os.getenv("CTN_USERNAME", "").strip()
    if not username:
        username = input("CTN username: ").strip()
    password = getpass.getpass("CTN password (not saved): ")
    if not username or not password:
        raise SystemExit("Need both username and password.")
    social = ctn.LegitSocial(timeout=20, max_retries=2)
    return social.login(username, password)


def start_lock():
    # Stops two copies of this script posting at the same time on this computer.
    folder.mkdir(exist_ok=True)
    try:
        fd = os.open(lock_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise SystemExit("Another publish may be running. Check before deleting data/posting.lock")
    try:
        os.write(fd, str(os.getpid()).encode())
    finally:
        os.close(fd)


def publish_draft(args):
    start_lock()
    try:
        queue = get_queue()
        item = find_draft(queue, args.id)
        if item["status"] != "approved":
            raise SystemExit("You can only publish approved drafts.")
        check_text(item["body"])

        last = queue.get("last_publish_at")
        if last:
            seconds = (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds()
            if seconds < COOLDOWN:
                raise SystemExit(f"Wait another {COOLDOWN - seconds:.0f} seconds before posting.")

        print("\nAbout to post:\n", item["body"])
        confirm = input(f"Type PUBLISH {item['id']} to continue: ").strip()
        if confirm != f"PUBLISH {item['id']}":
            print("Cancelled.")
            return

        account = sign_in()

        # If the request times out, it could still have been published.
        # Mark it uncertain BEFORE sending so we don't accidentally send it twice.
        item["status"] = "uncertain"
        item["publish_attempt_at"] = current_time()
        save_queue(queue)
        try:
            response = account.post(item["body"])
        except Exception as e:
            print("Couldn't confirm whether CTN received the post:", e)
            print("DON'T retry yet. Use the reconcile command or check your profile.")
            return

        if not isinstance(response, dict) or response.get("post_id") is None:
            print("CTN didn't return a post ID. Leave this as uncertain and check the website.")
            return

        # Reload in case the queue was updated while the request was processing
        queue = get_queue()
        item = find_draft(queue, args.id)
        item["status"] = "published"
        item["post_id"] = response.get("post_id")
        item["published_at"] = current_time()
        item["published_account_id"] = account.account_id
        queue["last_publish_at"] = current_time()
        save_queue(queue)
        print("Published! Post ID:", item["post_id"])
        if account.rate_limit:
            print("API quota reported after post:", account.rate_limit.remaining)
    finally:
        lock_file.unlink(missing_ok=True)


def reconcile_post(args):
    queue = get_queue()
    item = find_draft(queue, args.id)
    if item["status"] != "uncertain":
        raise SystemExit("This draft isn't marked uncertain.")
    account = sign_in()
    recent = list(account.account_posts(account.account_id, limit=50))
    same = [p for p in recent if str(p.get("body") or "").strip() == item["body"].strip()]
    if len(same) == 1:
        print("Found one matching post. Check that it is the one you just sent.")
        if input("Type MATCH to confirm: ").strip() != "MATCH":
            print("No changes made.")
            return
        item["status"] = "published"
        item["post_id"] = same[0]["post_id"]
        item["published_at"] = item.get("publish_attempt_at") or current_time()
        item["published_account_id"] = account.account_id
        queue["last_publish_at"] = current_time()
        save_queue(queue)
        print("Matched post ID:", item["post_id"])
    else:
        print(f"Found {len(same)} matching recent posts. Check the website manually; no new post sent.")


def measure_posts(args):
    items = [p for p in get_queue()["items"] if p["status"] == "published" and p.get("post_id")]
    items = items[-max(0, args.max_posts):] if args.max_posts > 0 else []
    if not items:
        print("Nothing published from this script yet.")
        return
    social = ctn.LegitSocial(timeout=20, max_retries=2)
    with open(metrics_file, "a", encoding="utf-8") as f:
        for item in items:
            try:
                counts = social.counters(int(item["post_id"]))
                record = {"measured_at": current_time(), "post_id": item["post_id"],
                          "draft_id": item["id"], "counters": counts}
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
                print("Post", item["post_id"], counts)
            except (ctn.APIError, OSError, ValueError) as e:
                print("Couldn't check", item["post_id"], e)
    print("Saved measurements. They are NOT official points.")


def show_summary(args):
    if not metrics_file.exists():
        print("No measurements. Run: posting.py measure")
        return
    saved = {}
    with open(metrics_file, encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            saved.setdefault(str(row["post_id"]), []).append(row)

    for post_id, entries in saved.items():
        entries.sort(key=lambda row: row["measured_at"])
        if len(entries) < 2:
            print("Post", post_id, "needs another measurement")
            continue
        old = entries[0]["counters"]
        new = entries[-1]["counters"]
        if not isinstance(old, dict) or not isinstance(new, dict):
            continue
        changes = {}
        for name in new:
            if name in old and type(old[name]) in (int, float) and type(new[name]) in (int, float):
                changes[name] = new[name] - old[name]
        print("Post", post_id, "changed by", changes)
    print("Official CTN score changes still need checking in the team portal.")


def add_score(args):
    folder.mkdir(exist_ok=True)
    entry = {"recorded_at": current_time(), "points": args.points,
             "note": args.note, "source": "manually entered from team portal"}
    with open(score_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    print("Saved manual score entry. Not checked against the portal.")


# Command setup is all at the bottom. Not pretty, but easy to find.
parser = argparse.ArgumentParser(description="CTN posting helper (manual approval)")
commands = parser.add_subparsers(dest="command", required=True)

draft = commands.add_parser("draft", help="Save a draft from a text file")
draft.add_argument("--file", required=True)
draft.add_argument("--author", default="")
draft.add_argument("--source", default="")
draft.set_defaults(func=new_draft)

listing = commands.add_parser("list", help="Show drafts")
listing.set_defaults(func=show_drafts)

approval = commands.add_parser("approve", help="Approve a draft after teammate review")
approval.add_argument("id")
approval.add_argument("--reviewer", required=True)
approval.set_defaults(func=approve_draft)

publishing = commands.add_parser("publish", help="Publish one approved draft")
publishing.add_argument("id")
publishing.set_defaults(func=publish_draft)

reconcile = commands.add_parser("reconcile", help="Check if a timed-out post was sent")
reconcile.add_argument("id")
reconcile.set_defaults(func=reconcile_post)

measure = commands.add_parser("measure", help="Read counters for your posts")
measure.add_argument("--max-posts", type=int, default=10)
measure.set_defaults(func=measure_posts)

summary = commands.add_parser("summary", help="Compare earlier and later counters")
summary.set_defaults(func=show_summary)

score = commands.add_parser("log-score", help="Record points from official activity log")
score.add_argument("--points", required=True, type=float)
score.add_argument("--note", required=True)
score.set_defaults(func=add_score)

if __name__ == "__main__":
    args = parser.parse_args()
    args.func(args)
