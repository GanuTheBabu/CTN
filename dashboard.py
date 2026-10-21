"""Real-Time Command & Reporting GUI for Capture the Narrative (CTN 2026).

Upgraded Features:
- Real-time auto-updating telemetry & estimated Red/Blue/Poll score calculator (`@st.fragment(run_every="4s")`)
- Auto-Provisioning & Suspension Replacement Controls (automatically creates believable Kingstonia personas
  using `team_code` as the password, replaces suspended accounts, and builds a full mutual-follow/like/repost mesh)
- Verified Creators War Room Tab (tracks all 11 Verified Creators, their stance, recent posts, and lets you
  aggressively @mention & reply to sway them and their followers)
- Full Newspaper Article Reader Tab (reads full markdown articles from Daily Kingstonia & Kingstonia Herald
  and turns them into nuanced persuasive posts)
- Natural Citizen Tone Slider (mixes neutral slice-of-life Kingstonia posts with persuasive posts)
"""

from __future__ import annotations

import csv
import json
import re
import subprocess
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pandas as pd
import streamlit as st

from core import AccountManager, DATA_DIR, Database, KINGSTONIA_PERSONAS, PROJECT_DIR, RateLimitScheduler, ctn
from worker import (
    FALLBACK_POST_TEMPLATES,
    HEARTBEAT_FILE,
    NEWS_CACHE_FILE,
    TRENDING_FILE,
    AutomationWorker,
    classify_post_stance,
    clean_post_text,
)

st.set_page_config(
    page_title="CTN 2026 — 24/7 Narrative Command Center",
    page_icon="📡",
    layout="wide",
)


@st.cache_resource
def get_system():
    db = Database()
    scheduler = RateLimitScheduler(db)
    accounts = AccountManager(db, scheduler)
    worker = AutomationWorker(db=db)
    return db, scheduler, accounts, worker


db, scheduler, account_mgr, worker = get_system()


@st.cache_resource
def ensure_background_daemon_thread():
    def _loop():
        while True:
            try:
                enabled = db.get_setting("daemon_enabled", "false") == "true"
                external_alive = False
                if HEARTBEAT_FILE.exists():
                    try:
                        hb = json.loads(HEARTBEAT_FILE.read_text(encoding="utf-8"))
                        if hb.get("pid") != subprocess.os.getpid() and (time.time() - float(hb.get("epoch", 0))) < 15:
                            external_alive = True
                    except Exception:
                        pass

                if enabled and not external_alive:
                    worker.run_single_cycle()
                elif not external_alive:
                    worker.write_heartbeat("paused")
            except Exception as e:
                db.log_action("error", f"Background thread error: {e}", status="error")

            interval = max(4, int(db.get_setting("post_interval_sec", "10") or 10))
            time.sleep(min(interval, 6))

    import threading
    t = threading.Thread(target=_loop, daemon=True, name="CTN_24_7_Daemon")
    t.start()
    return t


ensure_background_daemon_thread()


# ---------------------------------------------------------------------------
# Sidebar: 24/7 Server Controls, Fleet Target & Global Configuration
# ---------------------------------------------------------------------------

with st.sidebar:
    st.title("📡 CTN Command Center")
    st.caption("Campaign: **Vote NO on H2D2 / Keep Gates Open**")

    settings = db.get_all_settings()
    daemon_on = settings.get("daemon_enabled", "false") == "true"

    new_daemon_on = st.toggle(
        "🟢 24/7 Automation Server Running",
        value=daemon_on,
        help="When ON, continuously provisions accounts, mesh-follows, reads newspapers, replies to Verified Creators & NPCs, and saturates the 30/min like/view/follow quotas.",
    )
    if new_daemon_on != daemon_on:
        db.set_setting("daemon_enabled", "true" if new_daemon_on else "false")
        db.log_action("system", f"24/7 Automation Server turned {'ON' if new_daemon_on else 'OFF'} via GUI")
        st.rerun()

    st.subheader("🔑 Team Key (= Default Account Password)")
    saved_team_code = settings.get("team_code", "")
    team_code_input = st.text_input(
        "16-char CTN Team Code",
        value=saved_team_code,
        type="password",
        help="Used as API key for CompetitorLLM, team_key for registering accounts, AND default password for all newly created accounts.",
    )
    if team_code_input.strip() != saved_team_code:
        db.set_setting("team_code", team_code_input.strip())
        account_mgr.allowance_exhausted = False
        st.toast("Team code saved! Auto-provisioning ready.", icon="🔑")

    c_btn1, c_btn2 = st.columns(2)
    if c_btn1.button("⚡ Run 1 Cycle", use_container_width=True):
        with st.spinner("Running parallel bucket cycle..."):
            worker.run_single_cycle()
        st.toast("Parallel cycle finished!", icon="✅")

    if c_btn2.button("📊 Sync Views", use_container_width=True):
        with st.spinner("Polling counters..."):
            worker.update_metrics_if_due(force=True)
        st.toast("Views updated!", icon="📈")

    if st.button(
        "⏭️ Force Next Cycle (Skip Lead Post -> Next Lead Account)",
        type="primary",
        use_container_width=True,
        help="Immediately skips the current Lead Post, rotates to the NEXT Lead Account in the Top 5, publishes a brand-new Lead Post, and directs all 34 slave accounts to pour their views, likes, replies, and reposts into the new post!",
    ):
        with st.spinner("Rotating to next Lead Account & launching new Lead Post..."):
            res = worker.force_next_lead_cycle_now()
        st.toast(res.get("message", "Forced next Lead cycle!"), icon="⏭️")
        st.rerun()

    with st.expander("🧠 External Humanize AI API (Optional)", expanded=False):
        saved_hum_url = settings.get("humanize_api_url", "")
        saved_hum_key = settings.get("humanize_api_key", "")
        hum_url_in = st.text_input(
            "Humanizer / OpenAI-Compatible API URL",
            value=saved_hum_url,
            placeholder="https://api.openai.com/v1/chat/completions",
            help="Optional external API endpoint to humanize generated posts/replies. Built-in colloquial humanizer is always active even when blank.",
        )
        hum_key_in = st.text_input(
            "External Humanizer API Key",
            value=saved_hum_key,
            type="password",
        )
        if st.button("Save Humanize API", use_container_width=True):
            db.set_setting("humanize_api_url", hum_url_in.strip())
            db.set_setting("humanize_api_key", hum_key_in.strip())
            st.toast("Humanizer API settings saved!", icon="🧠")

    st.divider()
    st.subheader("🤖 Fleet & Natural Pacing")

    target_accs = st.slider(
        "Target Simultaneous Active Accounts",
        min_value=5,
        max_value=40,
        value=int(settings.get("target_active_accounts", "35") or 35),
        help="5 Primary Lead Accounts + 30 Supporter Accounts = 35 Total Accounts (every Lead Post gets 34 views, 34 likes, 34 replies, and 34 reposts!).",
    )
    swarm_on = st.checkbox(
        "🚀 Closed-Loop Top-5 Lead Swarm Mode (Hard 2-Post Counter)",
        value=settings.get("swarm_boost_enabled", "true") == "true",
        help="Cycles among the Top 5 Most-Viewed Lead Accounts. Each Lead Account posts 2 times, waiting on each post until 100% of other fleet accounts have viewed, liked, replied, and reposted it (prioritizing Likes & Replies before Reposts, and liking our own replies).",
    )
    neutral_pct = st.slider(
        "Neutral Slice-of-Life Post Ratio (%)",
        min_value=0,
        max_value=70,
        value=int(float(settings.get("neutral_tone_ratio", "0.15") or 0.15) * 100),
        step=5,
        help="Mixes everyday Kingstonia chatter (ferries, coffee, local scholarships) so accounts look 100% human and avoid unfollows (-20 pts).",
    )
    auto_prov = st.checkbox("Auto-Create Accounts up to Target", value=settings.get("auto_provision_accounts", "true") == "true")
    auto_repl = st.checkbox("Auto-Replace Suspended Accounts", value=settings.get("auto_replace_suspended", "true") == "true")
    auto_mesh = st.checkbox("Mutual Follow/Like/Repost Mesh", value=settings.get("auto_mesh_follow", "true") == "true")
    ver_focus = st.checkbox("Aggressively Target Verified Creators", value=settings.get("verified_creator_focus", "true") == "true")
    auto_post = st.checkbox("Auto-Publish Top-Level Posts", value=settings.get("auto_post_enabled", "true") == "true")
    auto_reply = st.checkbox("Auto-Reply to Shallow Threads (<7)", value=settings.get("auto_reply_enabled", "true") == "true")
    auto_like = st.checkbox("Saturate 30/min Likes, Views & Follows", value=settings.get("auto_like_enabled", "true") == "true")
    auto_report = st.checkbox("Blue-Team High-Confidence Bot Hunter", value=settings.get("auto_report_enabled", "true") == "true")

    if st.button("💾 Save Fleet & Strategy Settings", use_container_width=True):
        db.set_setting("target_active_accounts", str(target_accs))
        db.set_setting("swarm_boost_enabled", "true" if swarm_on else "false")
        db.set_setting("neutral_tone_ratio", str(round(neutral_pct / 100.0, 2)))
        db.set_setting("auto_provision_accounts", "true" if auto_prov else "false")
        db.set_setting("auto_replace_suspended", "true" if auto_repl else "false")
        db.set_setting("auto_mesh_follow", "true" if auto_mesh else "false")
        db.set_setting("verified_creator_focus", "true" if ver_focus else "false")
        db.set_setting("auto_post_enabled", "true" if auto_post else "false")
        db.set_setting("auto_reply_enabled", "true" if auto_reply else "false")
        db.set_setting("auto_like_enabled", "true" if auto_like else "false")
        db.set_setting("auto_report_enabled", "true" if auto_report else "false")
        account_mgr.allowance_exhausted = False
        st.toast("All settings saved!", icon="✅")

    live_refresh = st.toggle("🔄 Auto-Refresh Live Telemetry (4s)", value=True)


# ---------------------------------------------------------------------------
# Live Telemetry & Official Scoring Estimator Fragment (every 4 seconds)
# ---------------------------------------------------------------------------

def _parse_official_score_csv() -> Tuple[int, List[Dict[str, str]]]:
    """Read narrative-goat-activity.csv (if present) and sum the exact official points."""
    csv_path = PROJECT_DIR / "narrative-goat-activity.csv"
    if not csv_path.exists():
        return 0, []
    total_pts = 0
    rows_out: List[Dict[str, str]] = []
    try:
        with open(csv_path, encoding="utf-8", errors="replace") as f:
            reader = csv.DictReader(f)
            for row in reader:
                msg = str(row.get("Message") or "")
                m = re.search(r"([+-]?\d+)\s*points?", msg, re.IGNORECASE)
                if m:
                    total_pts += int(m.group(1))
                rows_out.append(row)
    except Exception:
        pass
    return total_pts, rows_out


def _format_duration_sec(sec: float) -> str:
    if sec <= 0:
        return "0m 0s"
    m = int(sec // 60)
    s = int(sec % 60)
    return f"{m}m {s}s"


def _render_live_telemetry():
    accounts = account_mgr.list_accounts()
    our_posts = db.get_our_posts(limit=500)
    rate_limits = scheduler.get_all_rate_limits()
    official_pts, csv_rows = _parse_official_score_csv()
    swarm_cycles = db.get_swarm_cycles(limit=200)

    total_views = sum(int(p.get("views") or 0) for p in our_posts)
    total_likes = sum(int(p.get("likes") or 0) for p in our_posts)
    total_replies = sum(int(p.get("replies") or 0) for p in our_posts)
    total_reposts = sum(int(p.get("reposts") or 0) for p in our_posts)

    active_accs = sum(1 for a in accounts if a.get("is_enabled") and a.get("status") == "active" and (a.get("token") or a.get("password")))
    target_accs_cfg = int(db.get_setting("target_active_accounts", "35") or 35)

    swarm_pid = int(db.get_setting("swarm_target_post_id", "0") or 0)
    swarm_lead_id = int(db.get_setting("swarm_lead_account_id", "0") or 0)
    swarm_hr_cnt = int(db.get_setting("swarm_posts_in_hour", "0") or 0)
    started_ts = float(db.get_setting("swarm_post_started_ts", "0") or 0.0)

    top_5_leads = worker.get_top_5_lead_accounts()
    ready_accs = worker._get_ready_accounts()
    lead_acc_row = next((a for a in ready_accs if int(a["account_id"]) == swarm_lead_id), (top_5_leads[0] if top_5_leads else None))
    lead_uname = f"@{lead_acc_row['username']}" if lead_acc_row else f"ID #{swarm_lead_id}"
    lead_dname = (lead_acc_row.get("display_name") or "") if lead_acc_row else ""

    supporters = [a for a in ready_accs if int(a["account_id"]) != swarm_lead_id]
    sw_status = worker.get_swarm_completion_status(swarm_pid, swarm_lead_id, supporters) if swarm_pid > 0 else {
        "total_supporters": len(supporters), "viewed_count": 0, "liked_count": 0, "replied_count": 0, "reposted_count": 0, "all_done": False
    }
    sup_n = max(1, sw_status["total_supporters"])

    # Calculate Average Time Taken to Complete a Post (from creating Lead Post until all slave accounts view, like, reply & repost)
    completed_cycles = [c for c in swarm_cycles if c.get("status") == "completed" and float(c.get("duration_sec") or 0) > 0]
    theoretical_sec = (2.0 * sup_n / 3.0) * 60.0  # 2N write actions (N replies + N reposts) at 3/min
    if completed_cycles:
        avg_completion_sec = sum(float(c["duration_sec"]) for c in completed_cycles) / len(completed_cycles)
        avg_label = _format_duration_sec(avg_completion_sec)
        avg_sub = f"Across {len(completed_cycles)} completed Lead Posts"
    else:
        # Estimate from current in-progress post velocity or 3/min rate limit
        elapsed_curr = max(1.0, time.time() - started_ts) if started_ts > 0 else 0.0
        writes_done = sw_status["replied_count"] + sw_status["reposted_count"]
        total_writes_needed = 2 * sup_n
        if writes_done >= 2 and elapsed_curr > 0:
            est_total_sec = (elapsed_curr / writes_done) * total_writes_needed
            avg_label = f"~{_format_duration_sec(est_total_sec)}"
            avg_sub = f"Live pace ({writes_done}/{total_writes_needed} writes done)"
        else:
            avg_label = f"~{_format_duration_sec(theoretical_sec)}"
            avg_sub = f"Est. at 3 writes/min ({total_writes_needed} writes)"

    hb_status = "Idle"
    if HEARTBEAT_FILE.exists():
        try:
            hb = json.loads(HEARTBEAT_FILE.read_text(encoding="utf-8"))
            age = int(time.time() - float(hb.get("epoch", 0)))
            hb_status = f"{hb.get('current_task', 'idle')} ({age}s ago)"
        except Exception:
            pass

    c1, c2, c3, c4, c5, c6, c7, c8 = st.columns(8)
    c1.metric("👑 Current Lead", lead_uname, f"Post {max(1, swarm_hr_cnt)}/2 ({lead_dname})")
    c2.metric("🚀 Active Lead Post", f"#{swarm_pid}" if swarm_pid else "Creating...", f"Elapsed: {_format_duration_sec(time.time() - started_ts) if started_ts > 0 else '0s'}")
    c3.metric("⏱️ Avg Post Completion", avg_label, avg_sub)
    c4.metric("👁️ Slave Views", f"{sw_status['viewed_count']} / {sup_n}", f"Total Views: {total_views:,}")
    c5.metric("❤️ Slave Likes", f"{sw_status['liked_count']} / {sup_n}", f"Total Likes: {total_likes:,}")
    c6.metric("💬 Slave Replies (1st)", f"{sw_status['replied_count']} / {sup_n}", f"Total Replies: {total_replies:,}")
    c7.metric("🔁 Slave Reposts (2nd)", f"{sw_status['reposted_count']} / {sup_n}", f"Total Reposts: {total_reposts:,}")
    c8.metric("🏅 Official Portal Score", f"{official_pts:,} pts", f"Fleet: {active_accs}/{target_accs_cfg}")

    # Active Lead Account & Ongoing Post Banner + Planned Instruction Queue Preview
    active_post_row = next((p for p in our_posts if int(p["post_id"]) == swarm_pid), None)
    active_post_body = active_post_row["body"] if active_post_row else "Waiting to publish Lead Post..."
    total_actions_done = sw_status["viewed_count"] + sw_status["liked_count"] + sw_status["replied_count"] + sw_status["reposted_count"]
    total_actions_req = max(1, 4 * sup_n)
    overall_pct = min(1.0, total_actions_done / total_actions_req)

    with st.container(border=True):
        b_col1, b_col2 = st.columns([3, 2])
        with b_col1:
            st.markdown(
                f"### 👑 Current Lead Account: **`{lead_uname}`** ({lead_dname} — ID `{swarm_lead_id}`)  |  "
                f"**Post `{max(1, swarm_hr_cnt)} / 2` in Cycle**  |  **Active Post ID: `#{swarm_pid}`**"
            )
            st.info(f"**Ongoing Lead Post #{swarm_pid}:**\n\n{active_post_body}")
            st.progress(
                overall_pct,
                text=(
                    f"Overall Swarm Completion for Post #{swarm_pid}: {int(overall_pct * 100)}% "
                    f"(Views {sw_status['viewed_count']}/{sup_n} • Likes {sw_status['liked_count']}/{sup_n} • "
                    f"Replies {sw_status['replied_count']}/{sup_n} • Reposts {sw_status['reposted_count']}/{sup_n})"
                ),
            )
            if st.button(
                "⏭️ Skip This Lead Post & Start Next Lead Account Cycle Now",
                key="banner_force_next_cycle_btn",
                type="primary",
                use_container_width=True,
                help="Click to immediately skip the current Lead Post, switch to the next Lead Account in the Top-5 rotation, publish its new Lead Post, and redirect all 34 slave accounts to boost the new post!",
            ):
                with st.spinner("Skipping current Lead Post and rotating to next Lead Account..."):
                    skip_res = worker.force_next_lead_cycle_now()
                st.toast(skip_res.get("message", "Moved to next Lead Account cycle!"), icon="⏭️")
                st.rerun()
        with b_col2:
            top5_badges = " ➡️ ".join(
                f"**🟢 @{a['username']}**" if int(a["account_id"]) == swarm_lead_id else f"@{a['username']}"
                for a in top_5_leads
            )
            st.markdown(f"**🔄 Top-5 Lead Rotation Order (by Views):**\n\n{top5_badges}")
            bucket_qs_banner = worker.build_planned_instruction_queue_by_bucket(per_bucket_limit=3)
            st.markdown("**⚡ 5 Parallel Rate-Limit Bucket Queues (Live Next Item per Bucket):**")
            for b_key, b_label in [
                ("post_write", "✍️ post_write (3/min)"),
                ("like", "❤️ like (30/min)"),
                ("view", "👁️ view (30/min)"),
                ("follow", "🤝 follow (30/min)"),
                ("feed_and_read", "📡 feed/read (300/min)"),
            ]:
                items = bucket_qs_banner.get(b_key, [])
                if items:
                    top_item = items[0]
                    st.markdown(f"- **`[{b_label}]`** `{top_item['Action']}` → {top_item['Target']} ({top_item['Actor Account']})")
                else:
                    st.markdown(f"- **`[{b_label}]`** *Standby*")

    with st.expander(f"⏱️ Live Server Heartbeat: **{hb_status}**  |  Click to inspect Team-Wide Rate Limit Buckets & Official Score Log", expanded=False):
        if rate_limits:
            rl_df = pd.DataFrame(rate_limits)[["bucket", "remaining", "limit_val", "wait_seconds", "updated_at"]]
            rl_df.columns = ["Route Bucket", "Remaining Quota", "Max / Min", "Cooldown Wait (s)", "Last Updated (UTC)"]
            st.dataframe(rl_df, hide_index=True, use_container_width=True)
        if csv_rows:
            st.markdown("**Official Activity Log (`narrative-goat-activity.csv`):**")
            st.dataframe(pd.DataFrame(csv_rows), hide_index=True, use_container_width=True)


if live_refresh and hasattr(st, "fragment"):
    @st.fragment(run_every="4s")
    def live_header_fragment():
        _render_live_telemetry()
    live_header_fragment()
else:
    _render_live_telemetry()


# ---------------------------------------------------------------------------
# Main Navigation Tabs
# ---------------------------------------------------------------------------

tab_plan, tab_archive, tab_accounts, tab_verified, tab_news, tab_analytics, tab_studio, tab_feed, tab_bot_network, tab_logs = st.tabs([
    "🎯 Strategy & Instruction Queue",
    "🗂️ All Posts Archive & Cycle History",
    "👥 Fleet & Auto-Creator",
    "⭐ Verified Creators War Room",
    "📰 Nuanced News Reader",
    "📈 Views & Scoring Analytics",
    "✍️ Content Studio & Queue",
    "🌐 Live Feed",
    "🕵️ Bot Network & Cluster Hunter",
    "📜 Real-Time Activity Logs",
])


# ===========================================================================
# TAB 0A: STRATEGY & LIVE INSTRUCTION QUEUE
# ===========================================================================
with tab_plan:
    st.subheader("🎯 Server Execution Strategy & Live Planned Instruction Queue")
    st.markdown(
        """
        **What the 24/7 Server is Doing Right Now (Closed-Loop Top-5 Lead Swarm + Enemy Verified Targeting Strategy):**
        1. **Top 5 Primary Lead Accounts**: The server ranks all active accounts by **Total Post Views** and selects the **Top 5 accounts** as Lead Accounts.
        2. **Trendy Lead Posts (News + Verified Creator Posts + Enemy `@mention`)**: Each Lead Account publishes **Post `1/2`** and **Post `2/2`** by combining the latest *Daily Kingstonia* / *Kingstonia Herald* newspaper articles with active **Verified Creator posts**, explicitly criticizing Enemy Verified Creators (`@DrAmaraSingh `, `@RealVictorHawthorne `) to sway neutral NPCs, and always injecting **`#keepkingstoniaopen`** + diverse tags.
        3. **100% Slave Completion Gate**: Before the Lead Account can advance to the next post, **every single slave/supporter account** (`34` accounts) completes all 4 actions on the active Lead Post:
           - **Immediate Views & Likes (`30/min` each)**: All slave accounts view and like the Lead Post first, then spend remaining quota liking & viewing our own replies **plus** external posts favoring our side (`Supporter Verified Creators` & `Vote-No` citizen posts).
           - **1st Write Priority — Short & Sweet Replies (`3/min`)**: Slave accounts post a short & sweet (1–2 sentence) reply combining their **persona life story + the Lead Post's specific topic**, with a **55% chance to `@mention` an Enemy Verified Creator** (`@DrAmaraSingh ` / `@RealVictorHawthorne `) to convert them, plus **`#keepkingstoniaopen`** and diverse supporting `#tags`.
           - **2nd Write Priority — LinkedIn-Structured Reposts (`3/min`)**: Once all 34 slave replies are done, slave accounts repost the Lead Post:
             - **30% of slave accounts** include a **LinkedIn-style structured commentary** (Hook $\rightarrow$ 2–3 bullet points $\rightarrow$ Call to action + `#keepkingstoniaopen` + diverse tags).
             - **70% of slave accounts** include **`#keepkingstoniaopen` + diverse supporting `#tags` only** (no prose description).
             - While waiting on the `3/min` repost rate limit, slave accounts continuously queue and execute `like` (`30/min`) and `view` (`30/min`) actions on posts favoring our side!
        4. **Lead Rotation**: Once **Post `2/2`** reaches 100% completion across all slave accounts, Lead privilege automatically rotates to the next account in the Top 5!
        """
    )

    st.markdown("#### ⚡ 5 Independent Parallel Rate-Limit Queues (Dispatched Simultaneously via `ThreadPoolExecutor`)")
    bucket_queues = worker.build_planned_instruction_queue_by_bucket(per_bucket_limit=35)
    rl_map = {r["bucket"]: r for r in scheduler.get_all_rate_limits()}

    q_col1, q_col2, q_col3 = st.columns(3)
    with q_col1:
        pw_rl = rl_map.get("post_write", {})
        st.markdown(f"**✍️ 1. `post_write` Queue (`3/min` — Wait: `{pw_rl.get('wait_seconds', 0.0)}s`)**")
        if bucket_queues.get("post_write"):
            st.dataframe(pd.DataFrame(bucket_queues["post_write"]), hide_index=True, use_container_width=True, height=260)
        else:
            st.caption("No pending post_write tasks.")

    with q_col2:
        lk_rl = rl_map.get("like", {})
        st.markdown(f"**❤️ 2. `like` Queue (`30/min` — Wait: `{lk_rl.get('wait_seconds', 0.0)}s`)**")
        if bucket_queues.get("like"):
            st.dataframe(pd.DataFrame(bucket_queues["like"]), hide_index=True, use_container_width=True, height=260)
        else:
            st.caption("No pending like tasks.")

    with q_col3:
        vw_rl = rl_map.get("view", {})
        st.markdown(f"**👁️ 3. `view` Queue (`30/min` — Wait: `{vw_rl.get('wait_seconds', 0.0)}s`)**")
        if bucket_queues.get("view"):
            st.dataframe(pd.DataFrame(bucket_queues["view"]), hide_index=True, use_container_width=True, height=260)
        else:
            st.caption("No pending view tasks.")

    q_col4, q_col5 = st.columns(2)
    with q_col4:
        fl_rl = rl_map.get("follow", {})
        st.markdown(f"**🤝 4. `follow` Queue (`30/min` — Wait: `{fl_rl.get('wait_seconds', 0.0)}s`)**")
        if bucket_queues.get("follow"):
            st.dataframe(pd.DataFrame(bucket_queues["follow"]), hide_index=True, use_container_width=True, height=160)
        else:
            st.caption("No pending follow tasks.")

    with q_col5:
        fd_rl = rl_map.get("feed", {})
        st.markdown(f"**📡 5. `feed` (`10/min`) + `read_other` (`300/min`) + `report_post` (`3/min`) Queue (Wait: `{fd_rl.get('wait_seconds', 0.0)}s`)**")
        if bucket_queues.get("feed_and_read"):
            st.dataframe(pd.DataFrame(bucket_queues["feed_and_read"]), hide_index=True, use_container_width=True, height=160)
        else:
            st.caption("No pending feed/read tasks.")

    with st.expander("📋 Combined Master Instruction Queue (All Buckets Merged)", expanded=False):
        q_items = worker.build_planned_instruction_queue(limit=120)
        if q_items:
            st.dataframe(pd.DataFrame(q_items), hide_index=True, use_container_width=True)
        else:
            st.info("No pending instructions in queue.")

    st.divider()
    st.subheader("🕸️ Slave-to-Lead Semantic Compatibility Graph & Distance-Based Reply Sizing")
    st.markdown(
        """
        **How the Graph Distance Engine Controls Reply Length, Verified Memories & `@mention` Variance:**
        - **Central Hub Node**: The active **Lead Post** (`swarm_target_post_id`).
        - **34 Slave Citizen Nodes**: Positioned at semantic distance $d \\in [0.0, 1.0]$ based on keyword, domain-cluster, and Verified-Creator-memory overlap with the Lead Post.
        - **Distance-Scaled Reply Tiers**:
          1. **🔥 Rank `1–5` (Top 5 Closest Slaves, smallest $d$)**: Generate a **Medium-to-Large (`220–340 chars`, 2–3 sentences)** reply weaving in their **Personal Verified Creator Memory** (`verified_memories`) and life story.
          2. **💬 Rank `6–14` (Moderate Distance)**: Generate a **Medium (`110–185 chars`, 1–2 sentences)** workplace/family angle.
          3. **⚡ Rank `15–24` (Farther Distance)**: Generate a **Short Punchy (`40–85 chars`, 5–10 words)** local reaction.
          4. **✨ Rank `25+` (Farthest Distance, largest $d$)**: Generate an **Ultra-Short (`1–3 words` like `"Agreed!"`, `"Cool, 100%."`, `"Spot on!"`)** micro-reply.
        - **Verified Creator `@mention` Variance**: Replies naturally vary between **`0` mentions (~35%)**, **`1` mention (~40%)**, and **`2–3` mentions (~25%)**.
        """
    )

    ready_fleet = worker._get_ready_accounts()
    lead_id_g = int(db.get_setting("swarm_lead_account_id", "0") or 0)
    target_pid_g = int(db.get_setting("swarm_target_post_id", "0") or 0)
    lead_row_g = next((a for a in ready_fleet if int(a["account_id"]) == lead_id_g), ready_fleet[0] if ready_fleet else None)
    supporters_g = [a for a in ready_fleet if int(a["account_id"]) != (int(lead_row_g["account_id"]) if lead_row_g else -1)]
    lead_body_g = ""
    if target_pid_g > 0:
        for p in db.get_our_posts(limit=100):
            if int(p["post_id"]) == target_pid_g:
                lead_body_g = str(p.get("body") or "")
                break

    if lead_row_g and supporters_g:
        graph_nodes = worker.compute_slave_lead_graph(lead_body_g, supporters_g)

        # Build an interactive SVG radial network graph showing the Lead Post in the center and all 34 slaves at distance d
        import math
        svg_w, svg_h = 860, 440
        cx, cy = svg_w // 2, svg_h // 2
        svg_elements = [
            f'<svg viewBox="0 0 {svg_w} {svg_h}" width="100%" height="420" style="background:#0e1117;border-radius:12px;border:1px solid #30363d;">',
            f'<circle cx="{cx}" cy="{cy}" r="75" fill="none" stroke="#2ea043" stroke-dasharray="4,4" stroke-opacity="0.45"/>',
            f'<circle cx="{cx}" cy="{cy}" r="135" fill="none" stroke="#58a6ff" stroke-dasharray="4,4" stroke-opacity="0.35"/>',
            f'<circle cx="{cx}" cy="{cy}" r="190" fill="none" stroke="#8b949e" stroke-dasharray="3,3" stroke-opacity="0.25"/>',
        ]
        n_total = max(1, len(graph_nodes))
        for idx_n, gn in enumerate(graph_nodes):
            angle = (2.0 * math.pi * idx_n) / n_total
            # Radius proportional to distance d (Top-5 closest sit on inner ring!)
            tier = gn["tier"]
            if tier == "LONG_MEMORY":
                r_dist = 62 + (idx_n * 4)
                color = "#3fb950"
                node_r = 9
            elif tier == "MEDIUM_STORY":
                r_dist = 115 + ((idx_n % 4) * 8)
                color = "#58a6ff"
                node_r = 6.5
            elif tier == "SHORT_PUNCHY":
                r_dist = 158 + ((idx_n % 4) * 7)
                color = "#d29922"
                node_r = 5.5
            else:
                r_dist = 192 + ((idx_n % 3) * 6)
                color = "#8b949e"
                node_r = 4.5

            nx = cx + r_dist * math.cos(angle)
            ny = cy + r_dist * math.sin(angle)
            stroke_w = "2.2" if tier == "LONG_MEMORY" else "1.0"
            stroke_op = "0.75" if tier == "LONG_MEMORY" else "0.28"
            svg_elements.append(
                f'<line x1="{cx}" y1="{cy}" x2="{nx:.1f}" y2="{ny:.1f}" stroke="{color}" stroke-width="{stroke_w}" stroke-opacity="{stroke_op}"/>'
            )
            svg_elements.append(
                f'<circle cx="{nx:.1f}" cy="{ny:.1f}" r="{node_r}" fill="{color}" stroke="#ffffff" stroke-width="1"/>'
            )
            label_txt = f"#{gn['rank']} @{gn['username'][:12]} (d={gn['distance']:.2f})" if tier == "LONG_MEMORY" else f"@{gn['username'][:10]}"
            font_sz = "10" if tier == "LONG_MEMORY" else "8.5"
            font_wt = "bold" if tier == "LONG_MEMORY" else "normal"
            svg_elements.append(
                f'<text x="{nx + 10:.1f}" y="{ny + 3:.1f}" fill="{color}" font-size="{font_sz}" font-family="monospace" font-weight="{font_wt}">{label_txt}</text>'
            )

        # Central Lead Post Node
        svg_elements.append(f'<circle cx="{cx}" cy="{cy}" r="24" fill="#f85149" stroke="#ffffff" stroke-width="2.5"/>')
        svg_elements.append(
            f'<text x="{cx}" y="{cy - 3}" text-anchor="middle" fill="#ffffff" font-size="10" font-family="sans-serif" font-weight="bold">LEAD #{target_pid_g}</text>'
        )
        svg_elements.append(
            f'<text x="{cx}" y="{cy + 10}" text-anchor="middle" fill="#ffdcd7" font-size="8.5" font-family="monospace">@{lead_row_g["username"][:12]}</text>'
        )
        svg_elements.append("</svg>")
        st.markdown("".join(svg_elements), unsafe_allow_html=True)

        g_rows = []
        for gn in graph_nodes:
            acc_obj = gn["account"]
            g_rows.append({
                "Graph Rank": f"#{gn['rank']}",
                "Slave Account": f"@{gn['username']} ({gn['display_name']})",
                "Voting Opinion (-100..+100)": f"+{gn['opinion_score']:.1f} (Vote No)",
                "Graph Distance (d)": gn["distance"],
                "Similarity": gn["similarity"],
                "Assigned Reply Tier": gn["tier_label"],
                "Target Reply Size": gn["target_chars"],
                "Verified @Mentions": f"{gn['mention_count_target']} mention(s)",
                "Shared Themes": ", ".join(gn["shared_clusters"]) or "general town life",
                "Verified Creator Memory Preview": (acc_obj.get("verified_memories") or "").replace("\n", " | ")[:140] + "...",
            })
        st.dataframe(pd.DataFrame(g_rows), hide_index=True, use_container_width=True)


# ===========================================================================
# TAB 0B: ALL POSTS ARCHIVE & CYCLE COMPLETION HISTORY
# ===========================================================================
with tab_archive:
    st.subheader("🗂️ Complete Record of All Posts Made & Swarm Cycle Completion Times")

    swarm_cycles = db.get_swarm_cycles(limit=500)
    all_our_posts = db.get_our_posts(limit=2000)

    completed_c = [c for c in swarm_cycles if c.get("status") == "completed" and float(c.get("duration_sec") or 0) > 0]
    in_prog_c = [c for c in swarm_cycles if c.get("status") != "completed"]

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("📌 Total Lead Posts Tracked", len(swarm_cycles), f"{len(completed_c)} Completed • {len(in_prog_c)} In Progress")
    if completed_c:
        avg_s = sum(float(c["duration_sec"]) for c in completed_c) / len(completed_c)
        m2.metric("⏱️ Measured Avg Completion Time", _format_duration_sec(avg_s), "Create -> All Slaves View/Like/Reply/Repost")
    else:
        ready_n = max(1, len(worker._get_ready_accounts()) - 1)
        m2.metric("⏱️ Theoretical Completion Time", _format_duration_sec((2.0 * ready_n / 3.0) * 60.0), f"{2 * ready_n} writes @ 3/min")
    root_posts_cnt = sum(1 for p in all_our_posts if p.get("kind") == "post")
    replies_cnt = sum(1 for p in all_our_posts if p.get("kind") == "reply")
    reposts_cnt = sum(1 for p in all_our_posts if p.get("kind") == "repost")
    m3.metric("📝 Total Root Posts Made", root_posts_cnt, "Across all team accounts")
    m4.metric("💬 Total Replies & Reposts Made", f"{replies_cnt} replies / {reposts_cnt} reposts", f"{len(all_our_posts)} total records")

    st.markdown("#### 🚀 Section 1: Lead Post Swarm Cycles & Time Taken to Complete")
    if swarm_cycles:
        cycle_rows = []
        now_ts = time.time()
        for c in swarm_cycles:
            tot_sup = max(1, int(c.get("total_supporters") or 34))
            dur = float(c.get("duration_sec") or 0.0)
            if c.get("status") != "completed" and float(c.get("started_epoch") or 0) > 0:
                dur = max(0.0, now_ts - float(c["started_epoch"]))
                dur_str = f"⏳ {_format_duration_sec(dur)} (In Progress)"
            else:
                dur_str = f"✅ {_format_duration_sec(dur)}"
            cycle_rows.append({
                "Lead Post ID": f"#{c['post_id']}",
                "Lead Account": f"@{c['lead_username']} (ID {c['lead_account_id']})",
                "Cycle Slot": f"Post {c.get('post_number_in_lead', 1)}/2",
                "Status": "🟢 Completed" if c.get("status") == "completed" else "🔥 Boosting Now",
                "Time Taken": dur_str,
                "Slave Views": f"{c.get('views_done', 0)} / {tot_sup}",
                "Slave Likes": f"{c.get('likes_done', 0)} / {tot_sup}",
                "Slave Replies": f"{c.get('replies_done', 0)} / {tot_sup}",
                "Slave Reposts": f"{c.get('reposts_done', 0)} / {tot_sup}",
                "Started (UTC)": c.get("started_at", ""),
                "Completed (UTC)": c.get("completed_at") or "—",
                "Post Content": c.get("body", ""),
            })
        st.dataframe(pd.DataFrame(cycle_rows), hide_index=True, use_container_width=True)
    else:
        st.info("No Lead Post cycles recorded yet.")

    st.divider()
    st.markdown("#### 📚 Section 2: Master Record of All Posts, Replies & Reposts Made by Our Fleet")
    f_col1, f_col2 = st.columns(2)
    kind_filter = f_col1.selectbox("Filter by Record Type:", ["all", "post", "reply", "repost"])
    acc_filter = f_col2.selectbox(
        "Filter by Account:",
        ["all"] + sorted({str(p.get("username") or "") for p in all_our_posts if p.get("username")}),
    )

    filtered_posts = [
        p for p in all_our_posts
        if (kind_filter == "all" or p.get("kind") == kind_filter)
        and (acc_filter == "all" or p.get("username") == acc_filter)
    ]
    if filtered_posts:
        arch_df = pd.DataFrame(filtered_posts)[
            ["post_id", "username", "account_id", "kind", "target_post_id", "views", "likes", "replies", "reposts", "body", "created_at"]
        ]
        arch_df.columns = [
            "Post ID", "Username", "Account ID", "Kind", "Target Post ID", "👁️ Views", "❤️ Likes", "💬 Replies", "🔁 Reposts", "Full Text Content", "Created At (UTC)"
        ]
        st.dataframe(arch_df, hide_index=True, use_container_width=True)
        st.download_button(
            "📥 Download Full Posts Archive (CSV)",
            data=arch_df.to_csv(index=False).encode("utf-8"),
            file_name="ctn_all_posts_archive.csv",
            mime="text/csv",
        )
    else:
        st.info("No matching posts found.")


# ===========================================================================
# TAB 1: FLEET & AUTO-CREATOR
# ===========================================================================
with tab_accounts:
    st.subheader("👥 Believable Citizen Fleet & Mutual Follow/Like Mesh")
    st.caption(
        "Every new account is automatically created with a realistic Kingstonia name & backstory, "
        "uses your **Team Code as its password**, and automatically follows & likes all other team accounts."
    )

    col_act1, col_act2, col_act3 = st.columns(3)
    if col_act1.button("➕ Auto-Create 1 Believable Citizen Account Now", use_container_width=True):
        t_code = db.get_setting("team_code", "").strip()
        if not t_code:
            st.error("Please enter your 16-character Team Code in the left sidebar first!")
        else:
            try:
                acc = account_mgr.register_account(team_key=t_code)
                st.success(f"Created & mesh-followed @{acc.username} ({acc.display_name}, ID: {acc.account_id}) with Team Code as password!")
                st.rerun()
            except Exception as e:
                st.error(f"Could not create account: {e}")

    if col_act2.button("🤝 Force Mutual Mesh Follow Across All Accounts", use_container_width=True):
        with st.spinner("Linking all active team accounts..."):
            n_fol = account_mgr.mesh_follow_all()
        st.success(f"Completed {n_fol} new mutual follow links across your fleet!")

    if col_act3.button("🔄 Test & Re-Login All Accounts Using Team Code", use_container_width=True):
        t_code = db.get_setting("team_code", "").strip()
        if not t_code:
            st.error("Enter your Team Code in the sidebar first.")
        else:
            ok_cnt = 0
            for a in account_mgr.list_accounts():
                pw = a.get("password") or t_code
                try:
                    account_mgr.login_account(a["username"], pw)
                    ok_cnt += 1
                except Exception:
                    pass
            st.success(f"Verified/refreshed tokens for {ok_cnt} accounts!")
            st.rerun()

    accounts = account_mgr.list_accounts()
    our_posts = db.get_our_posts(limit=500)

    acc_views: Dict[int, int] = Counter()
    acc_likes_recv: Dict[int, int] = Counter()
    acc_replies_recv: Dict[int, int] = Counter()
    for p in our_posts:
        aid = int(p["account_id"])
        acc_views[aid] += int(p.get("views") or 0)
        acc_likes_recv[aid] += int(p.get("likes") or 0)
        acc_replies_recv[aid] += int(p.get("replies") or 0)

    if accounts:
        acc_rows = []
        for a in accounts:
            aid = int(a["account_id"])
            has_creds = bool(a.get("token") or a.get("password"))
            status_badge = (
                "🟢 Active" if (a.get("is_enabled") and has_creds and a.get("status") == "active")
                else ("🚫 Suspended (Replaced)" if a.get("status") == "suspended"
                      else ("🔑 Needs Password" if not has_creds else f"🟡 {a.get('status')}"))
            )
            op_val = float(a.get("opinion_score") or 70.0)
            acc_rows.append({
                "ID": aid,
                "Username": f"@{a['username']}",
                "Display Name": a.get("display_name") or "",
                "Opinion (-100..+100)": f"+{op_val:.1f} (Vote No)" if op_val > 0 else f"{op_val:.1f}",
                "Status": status_badge,
                "👁️ Views": acc_views[aid],
                "💬 Replies Recv": acc_replies_recv[aid],
                "❤️ Likes Recv": acc_likes_recv[aid],
                "👥 Followers": int(a.get("followers_count") or 0),
                "Following": int(a.get("following_count") or 0),
                "Posts Sent": int(a.get("posts_count") or 0),
                "Replies Sent": int(a.get("replies_count") or 0),
                "Likes Given": int(a.get("likes_count") or 0),
                "Persona": (a.get("persona_prompt") or "")[:60],
                "Verified Creator Memories": (a.get("verified_memories") or "").replace("\n", " | ")[:85],
            })
        st.dataframe(
            pd.DataFrame(acc_rows).sort_values(by="👁️ Views", ascending=False),
            hide_index=True,
            use_container_width=True,
        )

    with st.expander("🛠️ Edit Active Account (Change Username, Display Name, Opinion Score, Persona Story, or Verified Memories)"):
        c_left, c_right = st.columns(2)
        with c_left:
            st.markdown("#### ✏️ Update Existing Active Account (Live Server + Local DB)")
            sel_existing = st.selectbox(
                "Select Account to Modify:",
                options=accounts,
                format_func=lambda a: f"ID {a['account_id']}: @{a['username']} ({a.get('display_name') or 'No Display Name'} | Opinion +{float(a.get('opinion_score') or 70.0):.1f})",
            )
            if sel_existing:
                edit_uid = int(sel_existing["account_id"])
                new_uname = st.text_input("New Username (updates live on LegitSocial & in DB)", value=sel_existing["username"], key=f"un_{edit_uid}")
                new_dname = st.text_input("New Display Name", value=sel_existing.get("display_name") or "", key=f"dn_{edit_uid}")
                new_op = st.slider(
                    "Voting Side Opinion (-100 = Close Gates / Vote Yes, +100 = Keep Kingstonia Open / Vote No)",
                    min_value=-100.0,
                    max_value=100.0,
                    value=float(sel_existing.get("opinion_score") or 70.0),
                    step=0.5,
                    key=f"op_{edit_uid}",
                )
                new_story = st.text_area("Persona Life Story", value=sel_existing.get("persona_prompt") or "", height=90, key=f"st_{edit_uid}")
                new_mem = st.text_area("Strong Personal Memories with Verified Creators", value=sel_existing.get("verified_memories") or "", height=110, key=f"mem_{edit_uid}")
                if st.button("💾 Push Username, Display Name, Opinion & Memory Update Now", use_container_width=True):
                    account_mgr.update_account_persona(
                        account_id=edit_uid,
                        display_name=new_dname,
                        persona_prompt=new_story,
                        username=new_uname,
                        opinion_score=new_op,
                        verified_memories=new_mem,
                    )
                    st.success(f"Updated ID {edit_uid} to @{new_uname} ({new_dname}, Opinion +{new_op:.1f}) on live server and in all tables!")
                    st.rerun()

        with c_right:
            st.markdown("#### Custom Believable Persona Registration")
            preset = st.selectbox(
                "Choose a Kingstonia Citizen Archetype:",
                options=KINGSTONIA_PERSONAS,
                format_func=lambda p: f"{p['display_name']} (@{p['base_username']}) — {p['persona_prompt'][:50]}...",
            )
            r_user = st.text_input("Username (max 32 chars)", value=preset["base_username"])
            r_disp = st.text_input("Display Name", value=preset["display_name"])
            r_per = st.text_area("Persona Prompt", value=preset["persona_prompt"], height=80)
            if st.button("➕ Register Citizen with Team Code as Password"):
                t_code = db.get_setting("team_code", "").strip()
                if not t_code:
                    st.error("Set your 16-char Team Code in the sidebar first!")
                else:
                    try:
                        acc = account_mgr.register_account(
                            username=r_user,
                            password=t_code,
                            team_key=t_code,
                            display_name=r_disp,
                            persona_prompt=r_per,
                        )
                        st.success(f"Registered @{acc.username} ({acc.display_name}) with Team Code as password!")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Registration error: {e}")


# ===========================================================================
# TAB 2: VERIFIED CREATORS WAR ROOM
# ===========================================================================
with tab_verified:
    st.subheader("⭐ Verified Creators Recovery & Enemy vs. Supporter Classification War Room")
    st.markdown(
        """
        **Active Verified Creator Intelligence & Targeting Rules:**
        1. **🔴 Enemy Verified Creators (`enemy_yes` — Target & Convert)**: `@DrAmaraSingh` and `@RealVictorHawthorne` push the H2D2 mandate. Every **Lead Post** targets and `@mentions` them directly, and **55% of slave replies** tag them to challenge their claims and convert their followers.
        2. **🟢 Supporter Verified Creators (`supporter_no` — Amplify & Like)**: `@muzhi_wong`, `@julio_morales`, `@lisa_podworth`, and `@MarinaCastilloCEO` oppose the mandate. While slave accounts wait on the `3/min` repost rate limit, they automatically spend spare `like` (`30/min`) and `view` (`30/min`) quota boosting these supporters' posts!
        3. **🟡 Swing Journalists & Media (`swing_journalist` — Cite & Frame)**: `@DailyKingstonia`, `@KingstoniaHerald`, `@amanda_rivera`, and `@gregorybrotherston` publish balanced/investigative articles (e.g., *"The Blank Cheque"*). Our Lead Posts combine their reporting with Verified Creator posts to frame trending topics.
        """
    )

    if st.button("🔄 Actively Recover All 11 Verified Creators & Classify All Their Posts Now", use_container_width=True):
        with st.spinner("Scanning all 11 Verified Creators and recovering their posts from LegitSocial..."):
            worker.sync_verified_creators_if_due(force=True)
        st.success("Recovered and classified all Verified Creator posts into Enemy vs. Supporter vs. Swing Media!")

    v_creators = db.get_verified_creators()
    if not v_creators or not worker._verified_posts:
        worker.sync_verified_creators_if_due(force=True)
        v_creators = db.get_verified_creators()

    by_camp = worker.get_verified_posts_by_camp()
    enemy_posts = by_camp.get("enemy_yes", [])
    supporter_posts = by_camp.get("supporter_no", [])
    swing_posts = by_camp.get("swing_journalist", [])

    vc_m1, vc_m2, vc_m3, vc_m4 = st.columns(4)
    vc_m1.metric("⭐ Total Verified Posts Recovered", len(worker._verified_posts), "Across all 11 Verified Creators")
    vc_m2.metric("🔴 Enemy Posts (Vote Yes)", len(enemy_posts), "@DrAmaraSingh • @RealVictorHawthorne")
    vc_m3.metric("🟢 Supporter Posts (Vote No)", len(supporter_posts), "@lisa_podworth • @MarinaCastilloCEO • @muzhi_wong • @julio_morales")
    vc_m4.metric("🟡 Swing Media / Journalist Posts", len(swing_posts), "@DailyKingstonia • @KingstoniaHerald • @amanda_rivera")

    if v_creators:
        vc_rows = []
        for vc in v_creators:
            st_code = vc.get("stance", "")
            if st_code == "opponent_yes":
                camp_badge = "🔴 ENEMY (Vote Yes — Target in Lead Posts & Replies)"
            elif st_code == "ally_no":
                camp_badge = "🟢 SUPPORTER (Vote No — Auto-Like & Amplify)"
            else:
                camp_badge = "🟡 SWING MEDIA (Cite Articles & Convert)"
            vc_rows.append({
                "ID": vc["account_id"],
                "Handle": f"@{vc['username']}",
                "Display Name": vc.get("display_name") or "",
                "Classification": camp_badge,
                "Our Replies": vc.get("our_replies_count", 0),
                "Our @Mentions": vc.get("our_mentions_count", 0),
                "Strategy Notes": vc.get("strategy_notes") or "",
            })
        st.dataframe(pd.DataFrame(vc_rows), hide_index=True, use_container_width=True)

    st.markdown("#### 📡 Recovered Posts by Verified Members — Enemy vs. Supporter Breakdown")
    camp_tab_all, camp_tab_enemy, camp_tab_supp, camp_tab_swing = st.tabs([
        f"📋 All Recovered Verified Posts ({len(worker._verified_posts)})",
        f"🔴 Enemy Verified Posts ({len(enemy_posts)})",
        f"🟢 Supporter Verified Posts ({len(supporter_posts)})",
        f"🟡 Swing Media Posts ({len(swing_posts)})",
    ])

    def _render_verified_post_list(posts_list, key_prefix: str):
        if not posts_list:
            st.info("No posts in this category yet. Click the Sync button above to recover posts.")
            return
        table_rows = []
        for vp in posts_list:
            camp = vp.get("verified_camp", "")
            badge = (
                "🔴 ENEMY (Vote Yes)" if camp == "enemy_yes"
                else ("🟢 SUPPORTER (Vote No)" if camp == "supporter_no" else "🟡 SWING MEDIA")
            )
            table_rows.append({
                "Post ID": f"#{vp['post_id']}",
                "Verified Author": f"@{vp.get('verified_username', '')}",
                "Display Name": vp.get("verified_display_name", ""),
                "Side / Alignment": badge,
                "Post Text": vp.get("body", ""),
                "Created At": str(vp.get("created_at", ""))[:19],
            })
        st.dataframe(pd.DataFrame(table_rows), hide_index=True, use_container_width=True)

        for vp in posts_list[:10]:
            vpid = int(vp["post_id"])
            vuser = vp.get("verified_username", "")
            camp = vp.get("verified_camp", "")
            badge = (
                "🔴 ENEMY (TARGET TO CONVERT)" if camp == "enemy_yes"
                else ("🟢 SUPPORTER (ALLY TO AMPLIFY)" if camp == "supporter_no" else "🟡 SWING JOURNALIST")
            )
            with st.container(border=True):
                st.markdown(f"**{badge}** — ⭐ **`@{vuser}`** — **Post `#{vpid}`** ({str(vp.get('created_at', ''))[:19]})")
                st.write(vp.get("body", ""))
                vc1, vc2, vc3 = st.columns(3)
                if vc1.button(f"💬 Short & Sweet Reply to @{vuser}", key=f"{key_prefix}_vrep_{vpid}"):
                    acc = worker.pick_next_account()
                    if not acc:
                        st.error("Authenticate an account first!")
                    else:
                        reply_txt = worker.generate_content("reply", acc.get("persona_prompt", ""), target_post=vp, tone="verified_pitch")
                        if worker._execute_reply(acc, vp, reply_txt):
                            db.increment_verified_engagement(int(vp["author_id"]), "our_replies_count")
                            st.success(f"Replied as @{acc['username']}: {reply_txt}")

                if vc2.button(f"❤️ Like @{vuser}'s Post", key=f"{key_prefix}_vlk_{vpid}"):
                    acc = worker.pick_next_account()
                    if acc:
                        try:
                            account_mgr.execute_with_account(int(acc["account_id"]), "like", lambda a: a.like(vpid))
                            db.record_interaction(int(acc["account_id"]), vpid, "like")
                            st.toast(f"Liked #{vpid}!", icon="❤️")
                        except Exception as e:
                            st.error(str(e))

                if vc3.button(f"🔁 LinkedIn Repost #{vpid}", key=f"{key_prefix}_vrt_{vpid}"):
                    acc = worker.pick_next_account()
                    if acc:
                        comment = worker.build_repost_comment(acc, vp, repost_index=0)
                        try:
                            res = account_mgr.execute_with_account(
                                int(acc["account_id"]),
                                "post_write",
                                lambda a: a.repost(vpid, comment=comment),
                            )
                            db.record_our_post(int(res["post_id"]), int(acc["account_id"]), acc["username"], "repost", comment, target_post_id=vpid)
                            st.success(f"Reposted with LinkedIn structure + #keepkingstoniaopen: {comment}")
                        except Exception as e:
                            st.error(str(e))

    with camp_tab_all:
        _render_verified_post_list(worker._verified_posts, "all")
    with camp_tab_enemy:
        _render_verified_post_list(enemy_posts, "enemy")
    with camp_tab_supp:
        _render_verified_post_list(supporter_posts, "supp")
    with camp_tab_swing:
        _render_verified_post_list(swing_posts, "swing")


# ===========================================================================
# TAB 3: NUANCED NEWS READER
# ===========================================================================
with tab_news:
    st.subheader("📰 In-Universe Newspaper Reader (`Daily Kingstonia` & `Kingstonia Herald`)")
    st.caption(
        "Reads the full markdown body of every article on `sites.legitreal.com` so our accounts cite real "
        "journalists (`@muzhi_wong`, `@amanda_rivera`), real institutions (Crown Harbour Tourism Board, QuokkaSec, "
        "Kingstonia University), and specific policy flaws."
    )

    if st.button("🔄 Read Full Articles from Daily Kingstonia & Kingstonia Herald Now"):
        with st.spinner("Downloading full article bodies..."):
            worker.sync_news_if_due(force=True)
        st.success(f"Loaded {len(worker._cached_news)} full articles!")

    articles = worker._cached_news
    if not articles:
        st.info("Click the button above to fetch the latest full newspaper articles.")
    else:
        for idx, art in enumerate(articles):
            site_name = art.get("site", {}).get("name", "Newspaper")
            title = art.get("title", "")
            byline = art.get("byline", "")
            url = art.get("full_url", "")
            body_md = art.get("full_body") or art.get("summary") or ""
            with st.expander(f"📰 [{site_name}] **{title}** — by {byline}", expanded=(idx < 2)):
                st.caption(f"URL: {url}  |  Published: {art.get('published_at', '')}")
                st.markdown(body_md)
                if st.button("✍️ Generate & Publish Nuanced Post Citing This Article", key=f"news_post_{idx}"):
                    acc = worker.pick_next_account()
                    if not acc:
                        st.error("Connect an account first!")
                    else:
                        old_news = worker._cached_news
                        worker._cached_news = [art]
                        text = worker.generate_content("post", acc.get("persona_prompt", ""), tone="persuasive")
                        worker._cached_news = old_news
                        if worker._execute_post(acc, text):
                            st.success(f"Published via @{acc['username']}: {text}")


# ===========================================================================
# TAB 4: VIEWS & SCORING ANALYTICS
# ===========================================================================
with tab_analytics:
    st.subheader("📈 Official Portal Score & Live Post Views / Trending Boost Analytics")
    official_pts, csv_rows = _parse_official_score_csv()
    st.info(
        f"**Official Score from `narrative-goat-activity.csv`: `{official_pts} points`** "
        "(Note: Only *NPC citizen* views/likes/replies/follows earn competition points; our own fleet's "
        "likes/replies/reposts boost our posts onto the Trending feed so more NPC citizens see and engage with them!)"
    )

    our_posts = db.get_our_posts(limit=250)

    if not our_posts:
        st.info("No team posts tracked yet.")
    else:
        df_posts = pd.DataFrame(our_posts)
        df_posts["Trending Velocity"] = (
            df_posts["views"] * 1 + df_posts["likes"] * 5 + df_posts["reposts"] * 10 + df_posts["replies"] * 15
        )

        col_chart1, col_chart2 = st.columns(2)
        with col_chart1:
            st.markdown("#### 🏆 Total Views, Likes & Replies by Account")
            by_acc = (
                df_posts.groupby("username")[["views", "likes", "replies", "reposts", "Trending Velocity"]]
                .sum()
                .sort_values(by="Trending Velocity", ascending=False)
            )
            st.bar_chart(by_acc[["views", "likes", "replies", "reposts"]])

        with col_chart2:
            st.markdown("#### 🔥 Top Swarm-Boosted Posts")
            top_p = df_posts.sort_values(by="Trending Velocity", ascending=False).head(10).copy()
            top_p["Label"] = "#" + top_p["post_id"].astype(str) + " (@" + top_p["username"] + ")"
            st.bar_chart(top_p.set_index("Label")[["views", "likes", "replies", "reposts"]])

        st.markdown("#### 📋 All Team Posts (Sorted by Trending Velocity & Views)")
        display_df = df_posts[
            ["post_id", "username", "kind", "Trending Velocity", "views", "replies", "likes", "reposts", "body", "created_at"]
        ].sort_values(by=["Trending Velocity", "views"], ascending=False)
        display_df.columns = [
            "Post ID", "Account", "Type", "🔥 Trending Score", "👁️ Total Views", "💬 Total Replies", "❤️ Total Likes", "🔁 Total Reposts", "Content", "Created At"
        ]
        st.dataframe(display_df, hide_index=True, use_container_width=True)


# ===========================================================================
# TAB 5: CONTENT STUDIO & QUEUE
# ===========================================================================
with tab_studio:
    st.subheader("✍️ AI Content Studio & Queue")
    col_gen, col_q = st.columns([1, 1])

    with col_gen:
        accounts_list = account_mgr.list_accounts()
        acc_choices = {f"@{a['username']} ({a.get('display_name') or 'Citizen'})": int(a["account_id"]) for a in accounts_list}
        acc_choices["Any Active Account (Round-Robin)"] = 0

        sel_acc_label = st.selectbox("Post as Account:", list(acc_choices.keys()))
        sel_acc_id = acc_choices[sel_acc_label]
        sel_tone = st.radio(
            "Post Style:",
            ["persuasive", "neutral_slice"],
            format_func=lambda t: "🎯 Nuanced News-Backed Persuasion (Vote No)" if t == "persuasive" else "☕ Neutral Everyday Kingstonia Slice-of-Life (Builds Trust)",
            horizontal=True,
        )

        if st.button("✨ Generate Post with CompetitorLLM + Full Newspapers"):
            persona = ""
            if sel_acc_id:
                row = account_mgr.get_account_row(sel_acc_id)
                persona = row.get("persona_prompt", "") if row else ""
            with st.spinner("Crafting authentic Kingstonia post..."):
                st.session_state["studio_draft"] = worker.generate_content(kind="post", persona_prompt=persona, tone=sel_tone)

        draft_body = st.text_area(
            "Post Text (max 480 chars, max 4 @mentions):",
            value=st.session_state.get("studio_draft", FALLBACK_POST_TEMPLATES[0]),
            height=130,
        )
        st.caption(f"Length: {len(draft_body)} / 480 characters")

        b1, b2 = st.columns(2)
        if b1.button("🚀 Publish Immediately", use_container_width=True):
            target_acc = account_mgr.get_account_row(sel_acc_id) if sel_acc_id else worker.pick_next_account()
            if not target_acc:
                st.error("No authenticated account available! Set your Team Code in the sidebar or sign in.")
            else:
                if worker._execute_post(target_acc, clean_post_text(draft_body, kind="post")):
                    st.success(f"Published live via @{target_acc['username']}!")

        if b2.button("📥 Queue for Auto-Publish", use_container_width=True):
            did = worker.enqueue_draft(
                body=draft_body,
                kind="post",
                assigned_account_id=sel_acc_id if sel_acc_id else None,
                status="approved",
                source="Studio GUI",
            )
            st.success(f"Queued `{did}`!")

    with col_q:
        st.markdown("#### 📋 Drafts & Scheduled Queue")
        with db._connect() as conn:
            q_rows = [dict(r) for r in conn.execute("SELECT * FROM drafts_queue ORDER BY created_at DESC LIMIT 25").fetchall()]
        if not q_rows:
            st.info("Queue is empty.")
        else:
            for item in q_rows:
                with st.container(border=True):
                    st.markdown(f"**`{item['id']}`** | **{item['status'].upper()}** | `{item['kind']}` | `{item.get('source', '')}`")
                    st.write(item["body"])


# ===========================================================================
# TAB 6: LIVE FEED & BOT HUNTER
# ===========================================================================
with tab_feed:
    st.subheader("🌐 Live Kingstonia Feed & High-Precision Bot Hunter")
    if st.button("🔄 Refresh Trending & Latest Feeds"):
        worker.sync_feeds_if_due(force=True)
        st.rerun()

    feed_posts = worker._cached_trending or []
    if not feed_posts and TRENDING_FILE.exists():
        try:
            feed_posts = json.loads(TRENDING_FILE.read_text(encoding="utf-8")).get("posts", [])
        except Exception:
            pass

    if feed_posts:
        stances = Counter(classify_post_stance(p.get("body", "")) for p in feed_posts if isinstance(p, dict))
        sc1, sc2, sc3 = st.columns(3)
        sc1.metric("🛡️ Anti-Mandate ('Vote No' / Allied)", stances.get("anti_mandate", 0))
        sc2.metric("⚠️ Pro-Mandate ('Vote Yes' / Persuade)", stances.get("pro_mandate", 0))
        sc3.metric("⚖️ Undecided / Neutral Citizens", stances.get("neutral", 0))

        for p in feed_posts[:20]:
            if not isinstance(p, dict):
                continue
            pid = int(p.get("post_id") or 0)
            aid = int(p.get("author_id") or 0)
            body = str(p.get("body") or "")
            stance = classify_post_stance(body)
            is_ver = aid in worker._verified_ids or aid <= 11
            badge = "⭐ **VERIFIED CREATOR**" if is_ver else (
                "🟢 **ALLIED (VOTE NO)**" if stance == "anti_mandate"
                else ("🔴 **PRO-MANDATE (PERSUADE)**" if stance == "pro_mandate" else "⚪ **UNDECIDED / NEUTRAL**")
            )
            with st.container(border=True):
                st.markdown(f"{badge} | **Post #{pid}** by `Author #{aid}`")
                st.write(body)
                b_like, b_rep, b_repost, b_bot = st.columns(4)
                if b_like.button("❤️ Like", key=f"flk_{pid}"):
                    acc = worker.pick_next_account()
                    if acc:
                        try:
                            account_mgr.execute_with_account(int(acc["account_id"]), "like", lambda a: a.like(pid))
                            db.record_interaction(int(acc["account_id"]), pid, "like")
                            st.toast(f"Liked #{pid}!", icon="❤️")
                        except Exception as e:
                            st.error(str(e))
                if b_rep.button("💬 Nuanced Reply", key=f"frp_{pid}"):
                    acc = worker.pick_next_account()
                    if acc:
                        r_txt = worker.generate_content("reply", acc.get("persona_prompt", ""), target_post=p)
                        if worker._execute_reply(acc, p, r_txt):
                            st.success(f"Replied as @{acc['username']}: {r_txt}")
                if b_repost.button("🔁 Repost", key=f"frt_{pid}"):
                    acc = worker.pick_next_account()
                    if acc:
                        try:
                            account_mgr.execute_with_account(int(acc["account_id"]), "post_write", lambda a: a.repost(pid))
                            db.record_interaction(int(acc["account_id"]), pid, "repost")
                            st.toast(f"Reposted #{pid}!", icon="🔁")
                        except Exception as e:
                            st.error(str(e))
                if not is_ver and b_bot.button("🚨 Report Bot (+25 Blue)", key=f"fbot_{pid}"):
                    acc = worker.pick_next_account()
                    if acc:
                        detail = (
                            "I think that this content is inauthentic (bot) activity because "
                            "it uses repetitive scripted campaign phrasing to flood the referendum feed."
                        )
                        worker._submit_post_report(int(acc["account_id"]), acc["username"], pid, "inauthentic", detail)
                        st.toast(f"Reported #{pid}!", icon="🚨")


# ===========================================================================
# TAB 6B: BOT NETWORK & CLUSTER HUNTER
# ===========================================================================
with tab_bot_network:
    st.subheader("🕵️ Coordinated Inauthentic Network & Bot Cluster Hunter")
    st.markdown(
        """
        **Blue-Team Network Forensics (Safe, Cluster-First Bot Detection):**
        - Scans trending posts to map **who likes, replies, and reposts together across multiple messages**.
        - Identifies **interconnected bot rings** (mutual followers, coordinated boosting, identical phrasing).
        - **100% Team Safeguard**: Cross-checks against `credentials.json` so our fleet is **never reported**.
        - Allows you to inspect any cluster, run deep forensic checks, review LLM bot evaluations, and report confirmed competitor bots with 1 click.
        """
    )

    import agent_analysis
    bot_analyzer = agent_analysis.NetworkAnalyzer()

    col_scan, col_status = st.columns([1, 2])
    with col_scan:
        if st.button("🔍 Scan Social Media & Pick Up Networks", use_container_width=True):
            with st.spinner("Scanning trending posts and mapping engagement networks..."):
                scan_res = bot_analyzer.scan_trending_and_build_clusters(trending_limit=10)
                st.session_state["bot_clusters_data"] = scan_res
                st.success(f"Discovered {scan_res.get('total_clusters_found', 0)} clusters across {scan_res.get('scanned_trending_posts', 0)} trending posts!")

    cluster_file = DATA_DIR / "bot_clusters.json"
    cluster_data = st.session_state.get("bot_clusters_data")
    if not cluster_data and cluster_file.exists():
        try:
            with open(cluster_file, "r", encoding="utf-8") as f:
                cluster_data = json.load(f)
        except Exception:
            pass

    if not cluster_data or not cluster_data.get("clusters"):
        st.info("No active cluster scans saved yet. Click **'Scan Social Media & Pick Up Networks'** above to detect bot clusters!")
    else:
        clusters = cluster_data.get("clusters", [])
        st.write(f"### Discovered Bot Groups ({len(clusters)} Groups)")
        
        for c in clusters:
            cid = c.get("cluster_id")
            c_name = c.get("name")
            c_size = c.get("size")
            c_stance = c.get("side_label")
            is_ours = c.get("is_our_team")
            boosted_pids = c.get("boosted_post_ids", [])
            bot_score = c.get("bot_confidence_score", 0.5)

            status_icon = "🛡️ OUR TEAM FLEET (PROTECTED)" if is_ours else ("🔴 ENEMY BOT CLUSTER" if "Enemy" in c_stance else "⚠️ SUSPECT CLUSTER")
            
            with st.expander(f"{status_icon} — {c_name} | {c_stance} | Confidence: {int(bot_score * 100)}%"):
                st.markdown(f"**Cluster Size**: `{c_size} accounts` | **Stance**: `{c_stance}`")
                st.markdown(f"**Co-Boosted Posts**: `{boosted_pids}`")
                
                if is_ours:
                    st.warning("⚠️ **SAFETY SHIELD ACTIVE**: This cluster contains accounts from your team fleet (`credentials.json`). Reporting is strictly locked.")
                
                # Show interconnected accounts in this cluster
                st.write("#### Interconnected Accounts in this Group:")
                acc_cols = st.columns(min(4, max(1, len(c.get("sampled_accounts", [])))))
                for idx, sa in enumerate(c.get("sampled_accounts", [])):
                    col = acc_cols[idx % len(acc_cols)]
                    with col:
                        st.markdown(f"**@{sa['username']}** (`ID {sa['account_id']}`)")
                        st.caption(f"Stance: {sa['dominant_stance']} | Posts: {sa['posts_count']}")
                        if sa.get("sample_post"):
                            st.text(f"\"{sa['sample_post'][:80]}...\"")

                # Deep Forensic Inspection Button
                deep_key = f"deep_inspect_{cid}"
                if st.button(f"🔬 Deep Inspect Group #{cid} (Check Mutual Follows, Past Posts & LLM Verdict)", key=deep_key):
                    with st.spinner(f"Running deep call on accounts in Group #{cid}..."):
                        deep_res = bot_analyzer.deep_analyze_cluster(c.get("account_ids", []))
                        st.session_state[f"deep_res_{cid}"] = deep_res

                deep_report = st.session_state.get(f"deep_res_{cid}")
                if deep_report:
                    st.divider()
                    st.write("#### 🔬 Deep Forensic Cluster Report:")
                    m1, m2, m3, m4 = st.columns(4)
                    m1.metric("Identical/Similar Posts", deep_report.get("similar_post_pairs", 0))
                    m2.metric("Mutual Follow Connections", deep_report.get("mutual_follow_connections", 0))
                    m3.metric("Older Posts Re-Boosted?", "YES (100% Coordinated)" if deep_report.get("older_posts_have_same_boosting") else "No")
                    m4.metric("Safe to Report?", "YES" if deep_report.get("can_report_safely") else "LOCKED")

                    llm_v = deep_report.get("llm_verification", {})
                    st.markdown(f"**🤖 CompetitorLLM Bot Verdict**: `{'BOT DETECTED' if llm_v.get('is_bot') else 'Uncertain'}` (Confidence: `{llm_v.get('confidence', 0.85)}`)")
                    st.markdown(f"**Reasoning**: *\"{llm_v.get('explanation') or llm_v.get('reason')}\"*")

                    # Display individual accounts and recent posts
                    st.write("##### Historical Posts from Accounts in this Group:")
                    for da in deep_report.get("accounts_analyzed", [])[:4]:
                        with st.container(border=True):
                            st.markdown(f"**@{da['username']}** (`ID {da['account_id']}`) — `{da['posts_found']} posts found` | Following in cluster: `{da['following_cluster_count']}`")
                            for pst in da.get("posts", [])[:2]:
                                p_body = pst.get("body") or f"[{pst.get('kind')} without body]"
                                st.write(f"- **#{pst.get('post_id')}** (`{pst.get('kind')}`): {p_body}")
                                if not da.get("is_our_team") and pst.get("post_id"):
                                    rep_pid = int(pst["post_id"])
                                    if st.button(f"🚨 Report Post #{rep_pid} as Inauthentic (+25 Blue)", key=f"drep_{cid}_{rep_pid}"):
                                        acc = worker.pick_next_account()
                                        if acc:
                                            rep_detail = f"I think that this content is inauthentic (bot) activity because account {da['username']} is part of a coordinated boosting cluster ({c_name}) with repeated scripted messages."
                                            worker._submit_post_report(int(acc["account_id"]), acc["username"], rep_pid, "inauthentic", rep_detail)
                                            st.toast(f"Reported #{rep_pid} to moderator!", icon="🚨")


# ===========================================================================
# TAB 7: REAL-TIME ACTIVITY LOGS
# ===========================================================================
with tab_logs:
    st.subheader("📜 Live 24/7 Server Activity Stream")
    log_filter = st.selectbox(
        "Filter by Action Type:",
        ["all", "post", "reply", "like", "follow", "repost", "report", "llm", "feed", "auth", "system", "error"],
    )
    logs = db.get_recent_logs(limit=150, action_type=log_filter)
    if logs:
        log_df = pd.DataFrame(logs)[["id", "timestamp", "username", "action_type", "target_id", "status", "detail"]]
        log_df.columns = ["ID", "Timestamp (UTC)", "Account", "Action", "Target ID", "Status", "Detail"]
        st.dataframe(log_df, hide_index=True, use_container_width=True)
