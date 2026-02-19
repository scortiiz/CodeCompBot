"""Helper functions for sheet operations and challenge logic."""

import logging
import os
from collections import defaultdict
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def is_admin(user_id: str) -> bool:
    """Check if user is an admin."""
    admin_ids = os.environ.get("ADMIN_SLACK_IDS", "").strip()
    if not admin_ids:
        return False
    return user_id in [uid.strip() for uid in admin_ids.split(",") if uid.strip()]


def get_team_points(ledger_ws) -> dict[str, int]:
    """Get current points per team from Ledger. Ledger: A=timestamp, B=team, C=points_delta."""
    try:
        rows = ledger_ws.get_all_records()
    except Exception:
        return {}
    points = defaultdict(int)
    for row in rows:
        team = row.get("team", "").strip()
        delta = row.get("points_delta", 0)
        try:
            delta = int(delta)
        except (TypeError, ValueError):
            delta = 0
        if team:
            points[team] += delta
    return dict(points)


def _row_val(row: dict, *keys: str, default: str = "") -> str:
    """Get value from row, trying multiple key variants (e.g. team, Team)."""
    for k in keys:
        v = row.get(k)
        if v is not None and str(v).strip():
            return str(v).strip()
    for k, v in row.items():
        if k and k.lower() in {x.lower() for x in keys} and v is not None and str(v).strip():
            return str(v).strip()
    return default


def get_all_teams(members_ws) -> list[str]:
    """Get unique team names from Members. Members: A=slack_user_id, B=name, C=team."""
    try:
        rows = members_ws.get_all_records()
    except Exception:
        return []
    teams = set()
    for row in rows:
        team = _row_val(row, "team", "Team")
        if team:
            teams.add(team)
    return sorted(teams)


def get_user_team(members_ws, slack_user_id: str) -> str | None:
    """Get team for a Slack user from Members."""
    try:
        rows = members_ws.get_all_records()
    except Exception:
        return None
    for row in rows:
        uid = _row_val(row, "slack_user_id", "Slack User ID")
        if uid == slack_user_id:
            team = _row_val(row, "team", "Team")
            return team or None
    return None


def get_challenges(challenges_ws) -> list[dict]:
    """Get all challenges. Challenges: A=challenge_key, B=challenge_name, C=points, D=min_num."""
    try:
        return challenges_ws.get_all_records()
    except Exception:
        return []


def get_unique_prefixes(challenges_ws) -> list[str]:
    """Get unique prefixes from challenge keys (e.g. SOC-001 -> SOC)."""
    prefixes = set()
    for c in get_challenges(challenges_ws):
        key = (c.get("challenge_key") or "").strip()
        if "-" in key:
            prefixes.add(key.split("-")[0].strip())
        elif key:
            prefixes.add(key[:3] if len(key) >= 3 else key)
    return sorted(prefixes)


def get_challenges_by_prefix(challenges_ws, prefix: str) -> list[dict]:
    """Get challenges whose challenge_key starts with prefix (e.g. SOC)."""
    prefix_upper = (prefix or "").strip().upper()
    return [
        c for c in get_challenges(challenges_ws)
        if ((c.get("challenge_key") or "").strip().upper().startswith(prefix_upper + "-") or
            (c.get("challenge_key") or "").strip().upper().startswith(prefix_upper))
    ]


def get_user_name(members_ws, slack_user_id: str) -> str:
    """Get display name for a Slack user from Members."""
    try:
        for row in members_ws.get_all_records():
            if _row_val(row, "slack_user_id", "Slack User ID") == slack_user_id:
                name = _row_val(row, "name", "Name")
                return name or f"<@{slack_user_id}>"
    except Exception:
        pass
    return f"<@{slack_user_id}>"


def get_reviewer_display_name(client, members_ws, user_id: str) -> str:
    """Resolve reviewer user_id to display name. Tries Members first, then Slack API."""
    name = get_user_name(members_ws, user_id)
    if name and not name.startswith("<@"):
        return name
    try:
        resp = client.users_info(user=user_id)
        if resp.get("ok") and resp.get("user"):
            u = resp["user"]
            return u.get("real_name") or u.get("profile", {}).get("real_name") or u.get("name", user_id)
    except Exception:
        pass
    return user_id


def get_random_challenge(challenges_ws, filter_keys: set[str] | None = None) -> dict | None:
    """Pick a random challenge. If filter_keys provided, only pick from those challenge_keys."""
    import random
    challenges = get_challenges(challenges_ws)
    if not challenges:
        return None
    if filter_keys is not None:
        challenges = [c for c in challenges if (c.get("challenge_key") or "").strip() in filter_keys]
    if not challenges:
        return None
    return random.choice(challenges)


def get_unclaimed_challenge_keys(challenges_ws, submissions_ws) -> set[str]:
    """Challenge keys that no team has completed (approved submission) yet."""
    approved = get_approved_submissions_by_team(submissions_ws)
    completed_keys = set()
    for subs in approved.values():
        for s in subs:
            key = (s.get("challenge_key") or "").strip()
            if key:
                completed_keys.add(key)
    all_keys = {(c.get("challenge_key") or "").strip() for c in get_challenges(challenges_ws) if (c.get("challenge_key") or "").strip()}
    return all_keys - completed_keys


def get_unclaimed_challenge_keys_for_team(challenges_ws, submissions_ws, team: str) -> set[str]:
    """Challenge keys that this specific team has not completed."""
    approved_by_team = get_approved_submissions_by_team(submissions_ws)
    completed_keys = set()
    for s in approved_by_team.get(team, []):
        key = (s.get("challenge_key") or "").strip()
        if key:
            completed_keys.add(key)
    all_keys = {(c.get("challenge_key") or "").strip() for c in get_challenges(challenges_ws) if (c.get("challenge_key") or "").strip()}
    return all_keys - completed_keys


def get_unclaimed_challenges_for_team_at_points(challenges_ws, submissions_ws, team: str, points: int) -> list[dict]:
    """Challenges (with challenge_name, min_num) that this team hasn't completed at given point value."""
    unclaimed_keys = get_unclaimed_challenge_keys_for_team(challenges_ws, submissions_ws, team)
    result = []
    for c in get_challenges(challenges_ws):
        try:
            c_pts = int(c.get("points", 0) or 0)
        except (TypeError, ValueError):
            c_pts = 0
        key = (c.get("challenge_key") or "").strip()
        if key and c_pts == points and key in unclaimed_keys:
            result.append(c)
    return result


def get_approved_submissions_by_team(submissions_ws) -> dict[str, list[dict]]:
    """Get approved submissions grouped by team. Submissions: D=team, H=status, I=challenge_key, J=points."""
    try:
        rows = submissions_ws.get_all_records()
    except Exception:
        return defaultdict(list)
    by_team = defaultdict(list)
    for row in rows:
        if (row.get("status", "").strip().lower() or "") != "approved":
            continue
        team = row.get("team", "").strip()
        if team:
            by_team[team].append(row)
    return dict(by_team)


def get_challenges_left_by_team(challenges_ws, submissions_ws, members_ws) -> dict[str, dict[int, int]]:
    """
    For each team, return {points_value: remaining_count}.
    Remaining = total challenges at that point value - approved submissions at that point value.
    """
    challenges = get_challenges(challenges_ws)
    approved_by_team = get_approved_submissions_by_team(submissions_ws)
    all_teams = get_all_teams(members_ws)

    # Build total challenges per point value
    points_to_challenges = defaultdict(set)
    for c in challenges:
        try:
            pts = int(c.get("points", 0) or 0)
        except (TypeError, ValueError):
            pts = 0
        key = c.get("challenge_key", "").strip()
        if key:
            points_to_challenges[pts].add(key)

    # Build completed per team per point value
    result = {}
    for team in all_teams:
        subs = approved_by_team.get(team, [])
        completed_by_points = defaultdict(set)
        for s in subs:
            try:
                pts = int(s.get("points", 0) or 0)
            except (TypeError, ValueError):
                pts = 0
            key = s.get("challenge_key", "").strip()
            if key:
                completed_by_points[pts].add(key)
        result[team] = {}
        for pts, keys in points_to_challenges.items():
            completed = completed_by_points.get(pts, set())
            result[team][pts] = len(keys) - len(keys & completed)

    return result


def add_submission(submissions_ws, submission_id: str, slack_user_id: str, team: str,
                   member_text: str, message_url: str, photo_url: str, challenge_key: str,
                   points: int) -> None:
    """Append a PENDING submission."""
    now = datetime.now(timezone.utc).isoformat()
    submissions_ws.append_row([
        submission_id, now, slack_user_id, team, member_text,
        message_url, photo_url, "PENDING", challenge_key, points, ""
    ])


def reset_ledger(ledger_ws) -> None:
    """Clear all rows in Ledger (keep header)."""
    try:
        rows = ledger_ws.get_all_values()
        if len(rows) <= 1:
            return
        range_to_clear = f"A2:F{len(rows)}"
        ledger_ws.batch_clear([range_to_clear])
    except Exception:
        raise


def reset_submissions(submissions_ws) -> None:
    """Clear all rows in Submissions (keep header)."""
    try:
        rows = submissions_ws.get_all_values()
        if len(rows) <= 1:
            return
        range_to_clear = f"A2:K{len(rows)}"
        submissions_ws.batch_clear([range_to_clear])
    except Exception:
        raise


def reset_queue(queue_ws) -> None:
    """Clear the queue ref so the next action posts a fresh queue message in Slack."""
    try:
        vals = queue_ws.get_all_values()
        if len(vals) >= 2:
            queue_ws.update("A2:B2", [["", ""]])
    except Exception:
        pass


def get_next_pending_submission(submissions_ws) -> dict | None:
    """Get oldest PENDING submission (first in queue)."""
    try:
        rows = submissions_ws.get_all_records()
        for row in rows:
            if (row.get("status", "").strip().lower() or "") == "pending":
                return row
    except Exception:
        pass
    return None


def get_pending_count(submissions_ws) -> int:
    """Count PENDING submissions."""
    try:
        rows = submissions_ws.get_all_records()
        return sum(1 for row in rows if (row.get("status", "").strip().lower() or "") == "pending")
    except Exception:
        return 0


def get_submission_by_id(submissions_ws, submission_id: str) -> dict | None:
    """Get submission row by submission_id."""
    try:
        rows = submissions_ws.get_all_records()
        for row in rows:
            if str(row.get("submission_id", "")).strip() == submission_id:
                return row
    except Exception:
        pass
    return None


def submission_exists_for_message(submissions_ws, message_ts: str) -> bool:
    """Check if we already have a submission for this challenge-channel message (SUB-{ts})."""
    return get_submission_by_id(submissions_ws, f"SUB-{message_ts}") is not None


def update_submission_status(submissions_ws, submission_id: str, status: str, reviewed_by: str = "",
                             challenge_key: str | None = None, points: int | None = None) -> bool:
    """Update submission status, reviewed_by, and optionally challenge_key/points. Returns True if updated."""
    try:
        rows = submissions_ws.get_all_values()
        for i, row in enumerate(rows):
            if i == 0:
                continue
            if len(row) > 0 and row[0] == submission_id:
                row_idx = i + 1
                submissions_ws.update_acell(f"H{row_idx}", status)
                submissions_ws.update_acell(f"K{row_idx}", reviewed_by)
                if challenge_key is not None:
                    submissions_ws.update_acell(f"I{row_idx}", str(challenge_key))
                if points is not None:
                    submissions_ws.update_acell(f"J{row_idx}", str(points))
                return True
    except Exception:
        pass
    return False


def add_ledger_entry(ledger_ws, team: str, points_delta: int, challenge_key: str,
                     submission_id: str, reviewed_by: str) -> None:
    """Append a ledger entry."""
    now = datetime.now(timezone.utc).isoformat()
    ledger_ws.append_row([now, team, points_delta, challenge_key, submission_id, reviewed_by])


def _to_slack_ts(val) -> str | None:
    """Convert value to Slack thread_ts string. Preserves format - Slack requires exact ts for threading."""
    if val is None or val == "":
        return None
    s = str(val).strip()
    if not s:
        return None
    try:
        f = float(s)
        # Use full 6 decimal places - Slack threading fails silently with abbreviated formats
        return f"{f:.6f}"
    except (TypeError, ValueError):
        return s


def get_queue_ref(queue_ws) -> tuple[str | None, str | None]:
    """Get stored queue message ref. Returns (message_ts, channel_id) or (None, None).
    Always returns strings - Slack's thread_ts must be a string or threading fails silently."""
    try:
        vals = queue_ws.get_all_values()
        if len(vals) >= 2 and len(vals[1]) >= 2:
            ts, ch = str(vals[1][0]).strip(), str(vals[1][1]).strip()
            if ts and ch:
                return ts, ch
    except Exception:
        pass
    return None, None


def update_queue_message(client, queue_ws, submissions_ws, review_channel_id: str, force_new: bool = False) -> tuple[str | None, str | None]:
    """Create or update the single review queue message.

    If force_new is True, post a fresh message (with Review button) and update the
    stored reference. Use "queue" / "resend queue" for that.

    When force_new is False (e.g. after each review), only UPDATE the existing
    queue message in place. Never post a new message so the channel stays clean.
    If no ref exists or update fails, returns existing ref for threading; no new post.
    """
    try:
        count = get_pending_count(submissions_ws)
        msg_ts, ch_id = get_queue_ref(queue_ws)
        text = f"Review queue: {count} pending"
        blocks = [
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*Review queue:* {count} pending"}},
            {"type": "actions", "elements": [{"type": "button", "text": {"type": "plain_text", "text": "Review"}, "style": "primary", "action_id": "open_review_modal", "value": "queue"}]},
        ]
        ch_str = str(ch_id).strip() if ch_id else ""
        ts_str = _to_slack_ts(msg_ts) if msg_ts else None

        if ts_str and ch_str and not force_new:
            try:
                client.chat_update(channel=ch_str, ts=ts_str, text=text, blocks=blocks)
                return ts_str, ch_str
            except Exception as e:
                logger.warning("chat_update failed (queue message not updated in place): %s", e)
                return ts_str, ch_str
        if force_new:
            resp = client.chat_postMessage(channel=review_channel_id, text=text, blocks=blocks)
            ts_new = _to_slack_ts(resp.get("ts") or resp.get("message", {}).get("ts"))
            if ts_new:
                set_queue_ref(queue_ws, ts_new, review_channel_id)
                return ts_new, review_channel_id
        return msg_ts and _to_slack_ts(msg_ts), ch_str or None
    except Exception:
        pass
    return None, None


def set_queue_ref(queue_ws, message_ts: str, channel_id: str) -> None:
    """Store queue message ref. Expects header row 1: message_ts, channel_id."""
    try:
        vals = queue_ws.get_all_values()
        if len(vals) < 2:
            queue_ws.update("A1:B2", [["message_ts", "channel_id"], [message_ts, channel_id]])
        else:
            queue_ws.update_acell("A2", message_ts)
            queue_ws.update_acell("B2", channel_id)
    except Exception:
        queue_ws.update("A1:B2", [["message_ts", "channel_id"], [message_ts, channel_id]])


def find_challenge_by_name(challenges_ws, name: str) -> dict | None:
    """Find a challenge by partial name match (case-insensitive). Tolerant of typos and truncation."""
    name_lower = (name or "").strip().lower()
    if not name_lower:
        return None
    challenges = get_challenges(challenges_ws)
    # Exact substring match first
    for c in challenges:
        challenge_name = (c.get("challenge_name", "") or "").lower()
        if name_lower in challenge_name or challenge_name in name_lower:
            return c
    # Fallback: all search words must appear in challenge name (handles "got boba toge" -> "got boba together")
    words = [w for w in name_lower.split() if len(w) >= 2]
    if words:
        for c in challenges:
            challenge_name = (c.get("challenge_name", "") or "").lower()
            if all(word in challenge_name for word in words):
                return c
    return None


def create_surprise_challenge(challenges_ws, challenge_name: str, points: int) -> str:
    """Create a SUP-XXX surprise challenge row and return its key."""
    try:
        challenges = get_challenges(challenges_ws)
    except Exception:
        challenges = []
    max_num = 0
    for c in challenges:
        key = (c.get("challenge_key") or "").strip().upper()
        if key.startswith("SUP-"):
            try:
                num = int(key.split("-")[-1])
                max_num = max(max_num, num)
            except (ValueError, IndexError):
                continue
    next_num = max_num + 1
    challenge_key = f"SUP-{next_num:03d}"
    try:
        challenges_ws.append_row([challenge_key, challenge_name, points, 0])
    except Exception:
        # Even if appending fails, still return the key so caller can handle/report
        pass
    return challenge_key
