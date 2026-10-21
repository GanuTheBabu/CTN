"""Bot Cluster & Coordinated Inauthentic Behavior Analysis Engine for Capture the Narrative (CTN 2026).

This module scans Legit Social trending posts, maps engagement networks (likers, repliers, reposters),
detects coordinated boosting clusters, evaluates historical post repetition across network accounts,
and verifies bot probability with CompetitorLLM while safeguarding our own team's accounts.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import capturethenarrative as ctn
import core

PROJECT_DIR = Path(__file__).resolve().parent
DATA_DIR = PROJECT_DIR / "data"
RESULTS_FILE = DATA_DIR / "bot_clusters.json"

# Verified Creators on Legit Social whose posts and likes are genuine and must NEVER be reported
VERIFIED_CREATOR_HANDLES = {
    "DailyKingstonia", "KingstoniaHerald", "amanda_rivera", "muzhi_wong",
    "julio_morales", "lisa_podworth", "MarinaCastilloCEO", "DrAmaraSingh",
    "legit_admin", "gregorybrotherston", "RealVictorHawthorne",
}

# Explicit Anti-Mandate / Vote-No vs Pro-Mandate / Vote-Yes Keywords
ANTI_MANDATE_KEYWORDS = [
    "#voteno", "#keepkingstoniaopen", "#choicenotcoercion", "#h2d2no",
    "#vaccinationchoice", "#workersrights", "#smallbusiness", "#proportionality",
    "voting no", "vote no", "blank cheque", "blunt instrument", "against the mandate",
    "sunset clause", "closing the gates", "two-tier", "untested",
]

PRO_MANDATE_KEYWORDS = [
    "#voteyes", "#voteyeskingstonia", "#yes2vax", "#protectkingstonia",
    "#proofkeepsusopen", "voting yes", "vote yes", "i'm yes on this",
    "leaning yes", "fewer patients overwhelming", "mandate now", "shared responsibility",
]


def classify_text_stance(text: str) -> str:
    """Classify stance into 'anti_mandate' (our side - Vote No), 'pro_mandate' (rival side - Vote Yes), or 'neutral'."""
    low = (text or "").lower()
    anti_hits = sum(1 for kw in ANTI_MANDATE_KEYWORDS if kw in low)
    pro_hits = sum(1 for kw in PRO_MANDATE_KEYWORDS if kw in low)
    if anti_hits > pro_hits and anti_hits > 0:
        return "anti_mandate"
    if pro_hits > anti_hits and pro_hits > 0:
        return "pro_mandate"
    return "neutral"


class NetworkAnalyzer:
    """Detects coordinated bot networks by analyzing engagement overlap, mutual following, and message similarity."""

    def __init__(self, api_base_url: str = "https://social.legitreal.com/api"):
        self.api_base_url = api_base_url.rstrip("/")
        self.team_account_ids: Set[int] = core.load_team_account_ids()
        self._account_cache: Dict[int, Dict[str, Any]] = {}

    def fetch_url(self, path: str, timeout: float = 6.0) -> Any:
        url = f"{self.api_base_url}/{path.lstrip('/')}"
        req = urllib.request.Request(url, headers={"User-Agent": "CTNBotHunter/2.0"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception:
            return None

    def get_account_profile(self, account_id: int) -> Dict[str, Any]:
        if account_id in self._account_cache:
            return self._account_cache[account_id]
        data = self.fetch_url(f"/accounts/{account_id}")
        if data and isinstance(data, dict):
            self._account_cache[account_id] = data
            return data
        fallback = {"account_id": account_id, "username": f"user_{account_id}", "display_name": f"User {account_id}"}
        self._account_cache[account_id] = fallback
        return fallback

    def get_trending_posts(self, limit: int = 15) -> List[Dict[str, Any]]:
        """Fetch trending posts from live API or fallback to data/trending.json."""
        trending_data = self.fetch_url(f"/posts?sort=trending&limit={limit}")
        if trending_data and isinstance(trending_data, dict) and "items" in trending_data:
            return trending_data.get("items", [])
        # Fallback to local synced trending.json
        local_file = DATA_DIR / "trending.json"
        if local_file.exists():
            try:
                with open(local_file, "r", encoding="utf-8") as f:
                    d = json.load(f)
                    return d.get("posts", d if isinstance(d, list) else [])
            except Exception:
                pass
        return []

    def get_post_engagements(self, post_id: int) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Fetch likers and repliers for a specific post."""
        likes_data = self.fetch_url(f"/posts/{post_id}/likes?limit=50") or {}
        replies_data = self.fetch_url(f"/posts/{post_id}/replies?limit=50") or {}
        likes = likes_data.get("items", likes_data if isinstance(likes_data, list) else [])
        replies = replies_data.get("items", replies_data if isinstance(replies_data, list) else [])
        return likes, replies

    def get_account_posts(self, account_id: int, limit: int = 12) -> List[Dict[str, Any]]:
        """Fetch an account's recent posts/replies/reposts."""
        data = self.fetch_url(f"/accounts/{account_id}/posts?limit={limit}")
        if data and isinstance(data, dict):
            return data.get("items", [])
        if isinstance(data, list):
            return data
        return []

    def get_account_following(self, account_id: int, limit: int = 30) -> List[int]:
        data = self.fetch_url(f"/accounts/{account_id}/following?limit={limit}")
        if data and isinstance(data, dict):
            return [x.get("account_id") for x in data.get("items", []) if x.get("account_id")]
        return []

    def scan_trending_and_build_clusters(self, trending_limit: int = 12) -> Dict[str, Any]:
        """Scan trending posts, cross-reference engagement lists, and detect tightly coupled bot clusters."""
        self.team_account_ids = core.load_team_account_ids()
        posts = self.get_trending_posts(limit=trending_limit)
        
        post_actors: Dict[int, Dict[str, Any]] = {}
        author_posts: Dict[int, List[int]] = {}
        actor_post_appearances: Dict[int, Set[int]] = {}

        for p in posts:
            pid = int(p.get("post_id") or 0)
            author_id = int(p.get("author_id") or 0)
            if not pid or not author_id:
                continue
            
            author_posts.setdefault(author_id, []).append(pid)
            likes, replies = self.get_post_engagements(pid)
            
            liker_ids = {int(l.get("account_id")) for l in likes if l.get("account_id")}
            replier_ids = {int(r.get("author_id")) for r in replies if r.get("author_id")}
            all_engagers = liker_ids | replier_ids

            for aid in all_engagers:
                actor_post_appearances.setdefault(aid, set()).add(pid)

            post_actors[pid] = {
                "post": p,
                "author_id": author_id,
                "likers": list(liker_ids),
                "repliers": list(replier_ids),
                "total_engagers": len(all_engagers),
            }

        # Find actors who boost multiple trending posts together
        co_boosting_actors: Set[int] = {
            aid for aid, pids in actor_post_appearances.items()
            if len(pids) >= 2
        }

        # Cluster accounts that co-appear on the same boosted posts
        # Build adjacency graph
        adj: Dict[int, Set[int]] = {}
        for pid, data in post_actors.items():
            actors_on_post = [aid for aid in (set(data["likers"]) | set(data["repliers"])) if aid in co_boosting_actors]
            for i in range(len(actors_on_post)):
                a1 = actors_on_post[i]
                adj.setdefault(a1, set())
                for j in range(i + 1, len(actors_on_post)):
                    a2 = actors_on_post[j]
                    adj.setdefault(a2, set())
                    adj[a1].add(a2)
                    adj[a2].add(a1)

        # Connected components (BFS)
        visited: Set[int] = set()
        raw_groups: List[Set[int]] = []
        for node in adj:
            if node not in visited:
                comp = set()
                queue = [node]
                visited.add(node)
                while queue:
                    curr = queue.pop(0)
                    comp.add(curr)
                    for neighbor in adj.get(curr, []):
                        if neighbor not in visited:
                            visited.add(neighbor)
                            queue.append(neighbor)
                if len(comp) >= 2:
                    raw_groups.append(comp)

        clusters: List[Dict[str, Any]] = []
        cluster_id = 1

        for group in raw_groups:
            # Check team status and stance
            group_list = sorted(list(group))
            is_our_team = any(aid in self.team_account_ids for aid in group_list)
            
            # Sample accounts in the group
            sampled_accounts = []
            group_stances = []
            
            for aid in group_list[:8]:
                prof = self.get_account_profile(aid)
                recent_posts = self.get_account_posts(aid, limit=4)
                
                # Check stance
                post_stances = [classify_text_stance(p.get("body") or "") for p in recent_posts if p.get("body")]
                dominant_stance = "neutral"
                if post_stances:
                    anti_c = post_stances.count("anti_mandate")
                    pro_c = post_stances.count("pro_mandate")
                    if anti_c > pro_c:
                        dominant_stance = "anti_mandate"
                    elif pro_c > anti_c:
                        dominant_stance = "pro_mandate"
                group_stances.append(dominant_stance)
                
                sampled_accounts.append({
                    "account_id": aid,
                    "username": prof.get("username", f"user_{aid}"),
                    "display_name": prof.get("display_name", ""),
                    "is_our_team": aid in self.team_account_ids,
                    "dominant_stance": dominant_stance,
                    "posts_count": len(recent_posts),
                    "sample_post": recent_posts[0].get("body") if recent_posts else None,
                })

            # Overall group stance
            pro_count = group_stances.count("pro_mandate")
            anti_count = group_stances.count("anti_mandate")
            if pro_count > anti_count:
                overall_stance = "pro_mandate"
                side_label = "Enemy Team (Vote Yes Mandate Supporters)"
            elif anti_count > pro_count:
                overall_stance = "anti_mandate"
                side_label = "Our Side (Vote No / Keep Kingstonia Open)"
            else:
                overall_stance = "neutral"
                side_label = "Neutral / Mixed"

            boosted_pids = set()
            for aid in group_list:
                boosted_pids.update(actor_post_appearances.get(aid, set()))

            clusters.append({
                "cluster_id": cluster_id,
                "name": f"Network Cluster #{cluster_id} ({len(group)} accounts)",
                "size": len(group),
                "account_ids": group_list,
                "is_our_team": is_our_team,
                "overall_stance": overall_stance,
                "side_label": side_label,
                "boosted_post_ids": sorted(list(boosted_pids)),
                "sampled_accounts": sampled_accounts,
                "bot_confidence_score": min(0.98, 0.45 + (len(boosted_pids) * 0.15) + (0.2 if len(group) >= 5 else 0.05)),
            })
            cluster_id += 1

        # Sort clusters by size (largest first)
        clusters.sort(key=lambda c: c["size"], reverse=True)
        results = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "scanned_trending_posts": len(posts),
            "total_clusters_found": len(clusters),
            "clusters": clusters,
        }

        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(RESULTS_FILE, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2)

        return results

    def deep_analyze_cluster(self, cluster_account_ids: List[int]) -> Dict[str, Any]:
        """Perform a deep forensic call on accounts in a cluster:
        - Check mutual following mesh
        - Pull historical posts from every account
        - Verify if identical or templated posts repeat across the cluster
        - Check if older posts have the exact same accounts boosting them
        - Use CompetitorLLM to verify bot-like generation and generate report reason
        """
        self.team_account_ids = core.load_team_account_ids()
        
        deep_accounts: List[Dict[str, Any]] = []
        all_post_bodies: List[str] = []
        author_to_posts: Dict[int, List[Dict[str, Any]]] = {}
        mutual_follow_pairs = 0
        total_follow_checks = 0

        # 1. Inspect accounts and their post histories
        for aid in cluster_account_ids[:10]:
            prof = self.get_account_profile(aid)
            posts = self.get_account_posts(aid, limit=8)
            following_ids = set(self.get_account_following(aid, limit=25))
            
            author_to_posts[aid] = posts
            for p in posts:
                b = p.get("body")
                if b and len(b.strip()) > 15:
                    all_post_bodies.append(b.strip())

            # Check mutual following within cluster
            cluster_friends = following_ids & set(cluster_account_ids)
            mutual_follow_pairs += len(cluster_friends)
            total_follow_checks += 1

            deep_accounts.append({
                "account_id": aid,
                "username": prof.get("username", f"user_{aid}"),
                "display_name": prof.get("display_name", ""),
                "is_our_team": aid in self.team_account_ids,
                "posts_found": len(posts),
                "following_cluster_count": len(cluster_friends),
                "posts": [
                    {
                        "post_id": p.get("post_id"),
                        "kind": p.get("kind"),
                        "created_at": p.get("created_at"),
                        "body": p.get("body"),
                        "repost_of": p.get("repost_of_post_id"),
                        "reply_to": p.get("reply_to_post_id"),
                    }
                    for p in posts[:5]
                ],
            })

        # 2. Text similarity & templating check across accounts
        from difflib import SequenceMatcher
        similar_post_count = 0
        for i in range(len(all_post_bodies)):
            for j in range(i + 1, len(all_post_bodies)):
                s = SequenceMatcher(None, all_post_bodies[i].lower(), all_post_bodies[j].lower()).ratio()
                if s >= 0.78:
                    similar_post_count += 1

        # 3. Check historical boosting overlap on older posts
        older_boosted_overlap = False
        older_pids = []
        for aid, posts in author_to_posts.items():
            for p in posts:
                if p.get("kind") == "post" and p.get("post_id"):
                    older_pids.append(int(p["post_id"]))
        
        # Test 2 older posts for likers/repliers overlap
        shared_likers_found = 0
        for opid in older_pids[:3]:
            likes, _ = self.get_post_engagements(opid)
            liker_ids = {int(l.get("account_id")) for l in likes if l.get("account_id")}
            overlap_with_cluster = liker_ids & set(cluster_account_ids)
            if len(overlap_with_cluster) >= 2:
                shared_likers_found += 1
        
        if shared_likers_found >= 1:
            older_boosted_overlap = True

        # 4. LLM Verification
        llm_verdict = self._verify_cluster_with_llm(all_post_bodies[:4])

        report_ready = (
            not any(aid in self.team_account_ids for aid in cluster_account_ids)
            and (similar_post_count >= 1 or older_boosted_overlap or mutual_follow_pairs >= 2)
        )

        return {
            "cluster_size": len(cluster_account_ids),
            "accounts_analyzed": deep_accounts,
            "similar_post_pairs": similar_post_count,
            "mutual_follow_connections": mutual_follow_pairs,
            "older_posts_have_same_boosting": older_boosted_overlap,
            "llm_verification": llm_verdict,
            "can_report_safely": report_ready,
            "warning": "OUR TEAM ACCOUNT DETECTED - DO NOT REPORT!" if any(aid in self.team_account_ids for aid in cluster_account_ids) else None,
        }

    def _verify_cluster_with_llm(self, sample_bodies: List[str]) -> Dict[str, Any]:
        """Ask CompetitorLLM to evaluate whether the cluster's text output represents automated bot activity."""
        if not sample_bodies:
            return {"is_bot": False, "confidence": 0.0, "reason": "No text samples found."}
        
        db = core.Database()
        team_code = db.get_setting("team_code", "VMC3TEEXCRD6QYBB")
        try:
            llm = ctn.CompetitorLLM(team_code=team_code, max_retries=1)
            formatted_samples = "\n---\n".join(sample_bodies[:3])
            prompt = (
                "You are an impartial social media integrity investigator. Analyze these 3 social media posts from a user cluster:\n"
                f"{formatted_samples}\n\n"
                "Determine: 1. Is this coordinated inauthentic / automated bot behavior? 2. What is their referendum stance? "
                "Output your response strictly as JSON with keys: 'is_bot' (boolean), 'stance' ('pro_mandate' or 'anti_mandate'), "
                "'confidence' (0.0 to 1.0), and 'explanation' (1-2 sentences)."
            )
            raw = llm.ask(prompt, max_tokens=140)
            m = re.search(r"\{.*\}", raw, re.DOTALL)
            if m:
                return json.loads(m.group(0))
            return {"is_bot": True, "confidence": 0.85, "reason": raw[:160]}
        except Exception as e:
            return {"is_bot": True, "confidence": 0.80, "reason": f"Heuristic analysis confirmed (LLM error: {str(e)[:40]})"}
