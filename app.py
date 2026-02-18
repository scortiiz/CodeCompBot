import os
import re
import logging
import json
from datetime import datetime, timezone
from dotenv import load_dotenv
import random

from slack_bolt import App

from listeners import register_listeners
from helpers import (
    is_admin,
    get_team_points,
    get_all_teams,
    get_user_team,
    get_user_name,
    get_challenges,
    get_random_challenge,
    get_challenges_left_by_team,
    get_unclaimed_challenge_keys,
    get_unclaimed_challenge_keys_for_team,
    get_unclaimed_challenges_for_team_at_points,
    get_unique_prefixes,
    get_challenges_by_prefix,
    get_pending_count,
    add_submission,
    reset_ledger,
    reset_submissions,
    reset_queue,
)
import gspread
from google.oauth2.service_account import Credentials

logging.basicConfig(level=logging.DEBUG)

# Initialization
load_dotenv()
app = App(
    token=os.environ.get("SLACK_BOT_TOKEN"),
    signing_secret=os.environ.get("SLACK_SIGNING_SECRET"),
)

CHALLENGE_CHANNEL_ID = os.environ["CHALLENGE_CHANNEL_ID"]
REVIEW_CHANNEL_ID = os.environ["REVIEW_CHANNEL_ID"]
SPREADSHEET_ID = os.environ["SPREADSHEET_ID"]

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# Prefer JSON credentials from an environment variable (for platforms like Render),
# but fall back to a file path when available.
credentials_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
service_account_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")

creds = None
if service_account_json:
    try:
        info = json.loads(service_account_json)
        creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    except json.JSONDecodeError:
        logging.error("GOOGLE_SERVICE_ACCOUNT_JSON is set but contains invalid JSON.")

if creds is None and credentials_path and os.path.exists(credentials_path):
    creds = Credentials.from_service_account_file(credentials_path, scopes=SCOPES)

if creds is None:
    raise RuntimeError(
        "Google service account credentials are not configured. "
        "Set GOOGLE_SERVICE_ACCOUNT_JSON to the JSON contents of your service account "
        "or provide a valid GOOGLE_APPLICATION_CREDENTIALS file path."
    )

gs_client = gspread.authorize(creds)
sheet = gs_client.open_by_key(SPREADSHEET_ID)

members_ws = sheet.worksheet("Members")
submissions_ws = sheet.worksheet("Submissions")
challenges_ws = sheet.worksheet("Challenges")
ledger_ws = sheet.worksheet("Ledger")

try:
    queue_ws = sheet.worksheet("Queue")
except Exception:
    queue_ws = sheet.add_worksheet(title="Queue", rows=10, cols=5)


def _normalize(text: str) -> str:
    """Collapse multiple spaces, strip, lowercase. Makes commands tolerant of typos/spaces."""
    return " ".join((text or "").split()).strip().lower()


def _format_standings(client, channel_id, thread_ts=None):
    """Format and post team standings. Excludes 'admin' team."""
    points = get_team_points(ledger_ws)
    teams = get_all_teams(members_ws)
    # Exclude admin from leaderboard
    teams = [t for t in teams if t.lower() != "admin"]
    # Include teams with 0 points
    for team in teams:
        if team not in points:
            points[team] = 0
    # Filter out admin from points too
    points = {t: p for t, p in points.items() if t.lower() != "admin"}
    sorted_teams = sorted(points.items(), key=lambda x: -x[1])
    lines = [f"• *{team}*: {pts} pts" for team, pts in sorted_teams]
    text = "*Leaderboard*\n\n" + "\n".join(lines) if lines else "*Leaderboards*\n\nNo teams made"
    kwargs = {"channel": channel_id, "text": text}
    if thread_ts:
        kwargs["thread_ts"] = thread_ts
    client.chat_postMessage(**kwargs)


def _resolve_team_from_list(team_input: str, team_list: list[str]) -> str | None:
    """Find team in list matching team_input (case-insensitive). Returns canonical name or None."""
    team_lower = (team_input or "").strip().lower()
    if not team_lower:
        return None
    for t in team_list:
        if t.lower() == team_lower:
            return t
    return None


def _resolve_team_case_insensitive(team_input: str, known_teams: dict) -> str | None:
    """Find team in known_teams matching team_input (case-insensitive). Returns canonical name or None."""
    team_lower = (team_input or "").strip().lower()
    if not team_lower:
        return None
    for key in known_teams:
        if key.lower() == team_lower:
            return key
    return None


def _parse_challenges_left_args(rest: str) -> tuple[str | None, int | None]:
    """Parse 'challenges left [team] [points]' -> (team_name or None, point_filter or None)."""
    rest = (rest or "").strip()
    if not rest:
        return None, None
    parts = rest.split()
    point_filter = None
    # Check if last token is a number (or second-to-last if last is 'pts')
    if parts:
        last = parts[-1].lower()
        if last == "pts" and len(parts) > 1:
            try:
                point_filter = int(parts[-2])
                parts = parts[:-2]
            except (ValueError, IndexError):
                pass
        else:
            try:
                point_filter = int(parts[-1])
                parts = parts[:-1]
            except (ValueError, IndexError):
                pass
    team_name = " ".join(parts).strip() if parts else None
    return team_name or None, point_filter


def _format_challenges_left(team_name: str | None, user_id: str, point_filter: int | None = None) -> str:
    """Format challenges left for a team. point_filter restricts to a specific point value."""
    left = get_challenges_left_by_team(challenges_ws, submissions_ws, members_ws)
    if team_name:
        team = _resolve_team_case_insensitive(team_name, left)
        if team is None:
            return f"Me when I spell wrong - *{team_name.strip()}* doesn't exist lol"
    else:
        team = get_user_team(members_ws, user_id)
        if not team:
            return "IVP oopsie, seems like you are not on a team... Try using 'challenges left [team name]'"
    by_points = left.get(team, {})
    if not by_points:
        return f"*{team}* Awk no challenges configured?"
    # Exclude negative points; optionally filter to specific point value
    items = [(pts, count) for pts, count in by_points.items() if pts >= 0 and count > 0]
    if point_filter is not None:
        items = [(pts, count) for pts, count in items if pts == point_filter]
    lines = [f"  {pts} points: {count} remaining" for pts, count in sorted(items)]
    if not lines:
        if point_filter is not None:
            return f"*{team}* – No {point_filter}-pt challenges left. YAYYY"
        return f"*{team}* ABSOLUTELY FANTASTICC!!! THIS IS WHAT CODEBASE IS ALL ABOUT!!!! CONGRATS ON COMPLETIING ALL THE CHALLENGESSSSSS"
    suffix = f" ({point_filter} points)" if point_filter is not None else ""
    out = f"*{team}* – challenges left{suffix}:\n" + "\n".join(lines)
    # When point filter set, show first 5 with challenge_name and min_num
    if point_filter is not None:
        unclaimed = get_unclaimed_challenges_for_team_at_points(challenges_ws, submissions_ws, team, point_filter)
        if unclaimed:
            detail_lines = []
            for i, c in enumerate(unclaimed[:5], 1):
                name = (c.get("challenge_name") or "?").strip()
                min_num = c.get("min_num", "?")
                detail_lines.append(f"  {i}. {name} (min {min_num} ppl)")
            out += "\n\n*First 5:*\n" + "\n".join(detail_lines)
    return out


def _update_queue_message(client, force_new: bool = False):
    """Create or update the single review queue message."""
    from helpers import update_queue_message
    from sheets import get_queue_ws, get_submissions_ws
    update_queue_message(client, get_queue_ws(), get_submissions_ws(), REVIEW_CHANNEL_ID, force_new=force_new)


def _process_challenge_message(client, channel_id: str, message_ts: str, user_id: str,
                               text_raw: str, files: list, logger) -> bool:
    """React to a challenge submission and add it to the queue. Returns True if added."""
    m = re.match(r"(?i)challenges?\s*(.*)", (text_raw or "").strip())
    member_text = (m.group(1).strip() if m else "").strip() or "_No description_"
    media_urls = []
    for f in files or []:
        url = f.get("url_private") or f.get("permalink", "")
        if url:
            media_urls.append({"url": url, "mimetype": f.get("mimetype", "")})
    photo_url_storage = "|".join(m.get("url", "") for m in media_urls)
    message_url = f"{channel_id}|{message_ts}"
    submission_id = f"SUB-{message_ts}"

    team = get_user_team(members_ws, user_id)
    if not team:
        return False

    try:
        num = random.randint(0, 1)
        client.reactions_add(
            channel=channel_id,
            timestamp=message_ts,
            name=["codecomp-1", "codecomp-2"][num],
        )
    except Exception as e:
        logger.warning("Failed to add reaction: %s", e)

    add_submission(
        submissions_ws,
        submission_id=submission_id,
        slack_user_id=user_id,
        team=team,
        member_text=member_text,
        message_url=message_url,
        photo_url=photo_url_storage,
        challenge_key="",
        points=0,
    )
    return True


@app.event("message")
def handle_message_events(event, client, logger):
    # 1️⃣ Ignore bot messages and edits – but allow file_share (messages with photo attachments)
    subtype = event.get("subtype")
    if subtype is not None and subtype != "file_share":
        return

    # 2️⃣ Pull out basic fields (normalize for forgiving command matching)
    text_raw = (event.get("text") or "").strip()
    text = _normalize(text_raw)
    channel_id = event.get("channel")
    user_id = event.get("user")
    files = event.get("files", [])
    message_ts = event.get("ts")
    thread_ts = event.get("thread_ts")

    # ---- Admin submit works in ANY channel (check first) ----
    if re.match(r"admin\s+submit\s+", text) and is_admin(user_id):
        match = re.match(r"admin\s+submit\s+", text)
        rest = text[match.end() :] if match else ""
        parts = rest.split(maxsplit=1)
        team_input = parts[0].strip() if len(parts) >= 1 else ""
        member_text = parts[1].strip() if len(parts) >= 2 else ""
        if not team_input:
            client.chat_postMessage(
                channel=channel_id,
                text="Usage: `admin submit [team] [description]` (include photo as attachment)",
                thread_ts=thread_ts or message_ts,
            )
            return
        teams = get_all_teams(members_ws)
        team = _resolve_team_from_list(team_input, teams)
        if team is None:
            client.chat_postMessage(
                channel=channel_id,
                text=f"Team *{team_input}* not found. These are the actual teams: {', '.join(teams)}",
                thread_ts=thread_ts or message_ts,
            )
            return
        media_urls = []
        for f in files or []:
            url = f.get("url_private") or f.get("permalink", "")
            if url:
                media_urls.append({"url": url, "mimetype": f.get("mimetype", "")})
        photo_url_storage = "|".join(m.get("url", "") for m in media_urls)
        submission_id = f"SUB-ADMIN-{message_ts}"
        message_url = ""
        try:
            add_submission(
                submissions_ws,
                submission_id=submission_id,
                slack_user_id=user_id,
                team=team,
                member_text=member_text,
                message_url=message_url,
                photo_url=photo_url_storage,
                challenge_key="",
                points=0,
            )
            client.chat_postMessage(
                channel=channel_id,
                text=f" Admin submission created for *{team}*. Open Review to assign challenge.",
                thread_ts=thread_ts or message_ts,
            )
        except Exception as e:
            logger.exception("Admin submit failed")
            client.chat_postMessage(
                channel=channel_id,
                text=f"❌ Failed to create submission: {e}",
                thread_ts=thread_ts or message_ts,
            )
        return

    if re.match(r"admin\s+submit\s+", text) and not is_admin(user_id):
        client.chat_postMessage(
            channel=channel_id,
            text="Only admins can submit for another team.",
            thread_ts=thread_ts or message_ts,
        )
        return

    # Only listen in challenge channel or review channel for other commands
    if channel_id not in (CHALLENGE_CHANNEL_ID, REVIEW_CHANNEL_ID):
        return

    # ---- Message commands (work in both channels) ----
    # Standings/leaderboard - allow typos: standing, standings, leader, leaderboard, leaderbord
    if text in ("standings", "standing", "leaderboard", "leader", "leaderbord"):
        _format_standings(client, channel_id, thread_ts or message_ts)
        return

    if text.startswith("challenge randomize") or text.startswith("challenge randomise"):
        # Normalize "challenge randomize" / "challenge randomise" + rest
        rest = text.replace("challenge randomise", "challenge randomize", 1)
        rest = rest.replace("challenge randomize", "", 1)
        rest = _normalize(rest)
        # Option 1: "challenge randomize" or "challenge randomize available" – no team has completed
        # Option 2: "challenge randomize team" or "challenge randomize my team" – user's team hasn't completed
        if rest in ("", "available", "unclaimed"):
            filter_keys = get_unclaimed_challenge_keys(challenges_ws, submissions_ws)
            label = "No team has completed"
        elif rest in ("team", "my team"):
            team = get_user_team(members_ws, user_id)
            if not team:
                client.chat_postMessage(
                    channel=channel_id,
                    text="Awk... You're not on a team. Try `challenge randomize` or `challenge randomize available` for unclaimed challenges.",
                    thread_ts=thread_ts or message_ts,
                )
                return
            filter_keys = get_unclaimed_challenge_keys_for_team(challenges_ws, submissions_ws, team)
            label = f"*{team}* hasn't completed"
        else:
            client.chat_postMessage(
                channel=channel_id,
                text="Try `challenge randomize available` – pick from challenges no team has done. `challenge randomize team` – pick from challenges your team hasn't done.",
                thread_ts=thread_ts or message_ts,
            )
            return
        ch = get_random_challenge(challenges_ws, filter_keys=filter_keys)
        if not ch:
            client.chat_postMessage(
                channel=channel_id,
                text="No matching challenges. All challenges may already be completed.",
                thread_ts=thread_ts or message_ts,
            )
        else:
            name = ch.get("challenge_name", "Unknown")
            key = ch.get("challenge_key", "")
            pts = ch.get("points", 0)
            client.chat_postMessage(
                channel=channel_id,
                text=f"BOOM BOOM BOOM *Random challenge* ({label}): {name} ({key}) – {pts} pts",
                thread_ts=thread_ts or message_ts,
            )
        return

    if text.startswith("challenges left") or text.startswith("challenge left"):
        rest = text.replace("challenges left", "", 1).replace("challenge left", "", 1)
        rest = _normalize(rest)
        team_name, point_filter = _parse_challenges_left_args(rest)
        msg = _format_challenges_left(team_name, user_id, point_filter)
        client.chat_postMessage(
            channel=channel_id,
            text=msg,
            thread_ts=thread_ts or message_ts,
        )
        return

    # ---- Reset semester (review channel only, admin) ----
    if channel_id == REVIEW_CHANNEL_ID and text == "reset semester" and is_admin(user_id):
        try:
            reset_ledger(ledger_ws)
            reset_submissions(submissions_ws)
            reset_queue(queue_ws)
            _update_queue_message(client, force_new=True)
            client.chat_postMessage(
                channel=channel_id,
                text="✅ Ledger, submissions, and queue reset for new semester. All team points and pending submissions cleared.",
                thread_ts=thread_ts or message_ts,
            )
        except Exception as e:
            logger.exception("Reset ledger failed")
            client.chat_postMessage(
                channel=channel_id,
                text=f"❌ Reset failed: {e}",
                thread_ts=thread_ts or message_ts,
            )
        return

    # ---- Queue: post/refresh review queue (review channel only, admin) ----
    if channel_id == REVIEW_CHANNEL_ID and text in ("queue", "resend queue", "resend review queue") and is_admin(user_id):
        try:
            # Force posting a brand new queue message with Review button
            _update_queue_message(client, force_new=True)
            client.chat_postMessage(
                channel=channel_id,
                text="✅ Review queue message refreshed.",
                thread_ts=thread_ts or message_ts,
            )
        except Exception as e:
            logger.exception("Resend queue failed")
            client.chat_postMessage(
                channel=channel_id,
                text=f"❌ Failed to resend queue: {e}",
                thread_ts=thread_ts or message_ts,
            )
        return

    # ---- Surprise challenge creation (review channel only, admin) ----
    # Usage: "surprise 5 3+ show up to CodeSoccer | free boba"
    # Points first, then name; optional "| prize" at the end.
    if channel_id == REVIEW_CHANNEL_ID and (text.startswith("surprise ") or text.startswith("suprise ")) and is_admin(user_id):
        from helpers import create_surprise_challenge

        # Use raw text to preserve casing in the challenge name/prize
        raw = (event.get("text") or "").strip()
        # Strip the first word ("surprise"/"suprise")
        parts_raw = raw.split(maxsplit=1)
        if len(parts_raw) < 2:
            client.chat_postMessage(
                channel=channel_id,
                text="Usage: `surprise [points] [challenge name] | [optional prize]`",
                thread_ts=thread_ts or message_ts,
            )
            return
        rest = parts_raw[1].strip()
        m = re.match(r"(\\d+)\\s+(.+)", rest)
        if not m:
            client.chat_postMessage(
                channel=channel_id,
                text="Usage: `surprise [points] [challenge name] | [optional prize]` (points must be a number).",
                thread_ts=thread_ts or message_ts,
            )
            return
        points = int(m.group(1))
        name_and_prize = m.group(2).strip()
        if "|" in name_and_prize:
            challenge_name, prize = [s.strip() for s in name_and_prize.split("|", 1)]
        else:
            challenge_name, prize = name_and_prize, ""

        challenge_key = create_surprise_challenge(challenges_ws, challenge_name, points)

        # Announce in the challenge channel
        desc_part = f"AND {challenge_name} SURPRISE!"
        if prize:
            desc_part = f"AND {challenge_name} – Prize: {prize} SURPRISE!"
        msg = f"<!channel> SURPRISE CHALLENGE COMPLETE THIS WEEK FOR {points} POINTS!!!! {desc_part}"
        client.chat_postMessage(channel=CHALLENGE_CHANNEL_ID, text=msg)

        # Confirm in review channel with the created key
        client.chat_postMessage(
            channel=channel_id,
            text=f"✅ Created surprise challenge *{challenge_key}* – {points} pts: {challenge_name}",
            thread_ts=thread_ts or message_ts,
        )
        return

    if text == "reset semester" and not is_admin(user_id):
        client.chat_postMessage(
            channel=channel_id,
            text="Only admins can reset the semester.",
            thread_ts=thread_ts or message_ts,
        )
        return

    # ---- Challenge channel only: challenge submission with photo ----
    # If they forget a photo/video, gently remind them.
    if channel_id == CHALLENGE_CHANNEL_ID and text.startswith("challenge") and not files:
        client.chat_postEphemeral(
            channel=channel_id,
            user=user_id,
            text="Awk... to submit a challenge, send `challenge [description]` **with a photo or video attached**.",
        )
        return

    if channel_id == CHALLENGE_CHANNEL_ID and text.startswith("challenge") and files:
        logger.info("Challenge submission detected")

        team = get_user_team(members_ws, user_id)
        if not team:
            client.chat_postMessage(
                channel=channel_id,
                text="Awk... I couldn't find your team in the Members sheet. Ask an admin to add you first.",
                thread_ts=thread_ts or message_ts,
            )
            return

        try:
            _process_challenge_message(
                client, channel_id, message_ts, user_id,
                event.get("text") or "", files, logger,
            )
        except Exception as e:
            logger.exception("Challenge submit failed")
            client.chat_postMessage(
                channel=channel_id,
                text=f"❌ Failed to create submission: {e}",
                thread_ts=thread_ts or message_ts,
            )
        return


# Register Listeners
register_listeners(app)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "3000"))
    app.start(host="0.0.0.0", port=port)
