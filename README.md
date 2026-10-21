# Capture the Narrative — team automation handoff

This is a **prototype**, not a finished scheduled or multi-user posting system.
The three Python files are the originals, renamed to remove `(1)` from filenames.

## Contents

- `main.py` — fetches up to 20 trending posts once per run and saves them to `data/trending.json` plus timestamped `data/history/*.json` snapshots.
- `dashboard.py` — Streamlit dashboard showing saved trending posts, keyword-based topic tagging and history. It makes **no API requests** itself.
- `posting.py` — command-line queue for draft creation, teammate approval, interactive publishing, post counter measurements and manually entered score logs.
- `draft.txt` — sample proposed post for review; **not** published automatically.
- `requirements.txt` — packages to install in an isolated Python environment.

## Local setup (Windows PowerShell)

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python main.py
python -m streamlit run dashboard.py
```

Run the posting steps separately in the same folder:

```powershell
python posting.py draft --file draft.txt --author YOUR_NAME
python posting.py list
python posting.py approve DRAFT_ID --reviewer TEAMMATE_NAME
python posting.py publish DRAFT_ID
python posting.py measure
python posting.py summary
```

`approve` and `publish` prompt for explicit typed confirmation. `publish` prompts for CTN username/password at runtime and does not save the password. Do not put credentials in the scripts or shared ZIP.

## What needs implementing for actual automation

1. **Shared queue & state:** `data/posting_queue.json` and the current lock/cooldown exist only on one computer. Use a shared durable store or one controlled worker; handle concurrent writes and duplicate jobs.
2. **Team-wide API rate limiting:** the existing 30-second local publish cooldown does **not** coordinate other teammates or different action types. Build one team-wide scheduler covering posts/replies/reposts, feed reads, reports, and LLM requests. Check the latest official limits. Respect HTTP 429 / Retry-After.
3. **Scheduling & unattended authentication:** `main.py` runs once and `posting.py publish` requires interactive confirmation/login. No unattended job runner is included. Design an approved, secure authentication mechanism under the competition rules; keep human review or another explicitly authorised approval gate.
4. **Idempotency & recovery:** `posting.py` marks attempts `uncertain` before posting and has a `reconcile` command. Preserve/extend this to avoid duplicate posts after timeouts or restarts.
5. **Tests/visibility:** check the SDK methods with `capturethenarrative==0.5.0`, add structured logs, failure alerts, and verify metrics against official competition scores. Dashboard topic counts are not persuasion scores.

## Suggested responsibility split

- **Content/review:** draft, verify claims and approve before release.
- **Automation:** shared queue, single scheduler, account/auth design, retry handling, logging.
- **Analytics:** snapshots, engagement changes, experiments and official score checks.

## Share safely

The ZIP contains **only source code and a proposed post**, not local `data/`, virtual environments, login tokens or `.env` files. Before committing to GitHub, review the `.gitignore` and repository privacy settings.
