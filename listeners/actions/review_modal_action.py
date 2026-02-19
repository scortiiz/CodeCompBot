"""Opens review modal and handles Accept/Reject for submissions."""

from logging import Logger

from slack_bolt import Ack
from slack_sdk import WebClient

from sheets import get_submissions_ws, get_ledger_ws, get_challenges_ws, get_members_ws, get_queue_ws

from helpers import (
    get_challenges,
    get_submission_by_id,
    get_next_pending_submission,
    get_user_name,
    get_reviewer_display_name,
    get_unique_prefixes,
    get_challenges_by_prefix,
    get_queue_ref,
    update_submission_status,
    update_queue_message,
    add_ledger_entry,
    is_admin,
    _row_val,
    _to_slack_ts,
)

import os

REVIEW_CHANNEL_ID = os.environ.get("REVIEW_CHANNEL_ID", "")
CHALLENGE_CHANNEL_ID = os.environ.get("CHALLENGE_CHANNEL_ID", "")


def _build_review_modal(submission: dict, submission_id: str, queue_msg_ts: str, queue_ch_id: str = "") -> dict:
    """Build modal with prefix dropdown, challenge dropdown (option_groups), Accept and Reject buttons."""
    challenges_ws = get_challenges_ws()
    prefixes = get_unique_prefixes(challenges_ws)
    option_groups = []
    for prefix in prefixes:
        ch_list = get_challenges_by_prefix(challenges_ws, prefix)
        options = [
            {
                "text": {"type": "plain_text", "text": (c.get('challenge_name', '') or '')[:75]},
                "value": f"{c.get('challenge_key', '')}|{c.get('points', 0)}",
            }
            for c in ch_list
        ]
        if options:
            option_groups.append({"label": {"type": "plain_text", "text": prefix}, "options": options})

    team = submission.get("team", "")
    submitter_id = submission.get("slack_user_id", "")
    members_ws = get_members_ws()
    submitter_name = get_user_name(members_ws, submitter_id)
    member_text = _row_val(submission, "member_text", "Member Text", "member text") or "_None_"

    blocks = [
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*Team:* {team}\n*Submitted by:* {submitter_name}\n*Description:* {member_text}"}},
    ]
    photo_url = (submission.get("photo_url") or "").strip()
    # Show images inline; show videos/other files as links you can click to view in Slack.
    image_exts = (".png", ".jpg", ".jpeg", ".gif", ".webp")
    other_links: list[str] = []
    for url in photo_url.split("|"):
        url = url.strip()
        if not url or not any(url.lower().startswith(prefix) for prefix in ("http://", "https://")):
            continue
        if url.lower().endswith(image_exts):
            blocks.append({"type": "image", "image_url": url, "alt_text": "Submission media"})
        else:
            other_links.append(url)

    if other_links:
        # We can't reliably embed videos in a modal, so just remind the reviewer
        # to look back at the original challenge message for any video/files.
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "🎥 This submission includes *video or other file attachments*. "
                            "Please view them in the original challenge message in the channel.",
                },
            }
        )
    blocks.append({
        "type": "section",
        "block_id": "challenge_block",
        "text": {"type": "mrkdwn", "text": "*Select challenge* (prefix → challenge):"},
        "accessory": {
            "type": "static_select",
            "action_id": "challenge_select",
            "placeholder": {"type": "plain_text", "text": "Choose a challenge"},
            "option_groups": option_groups,
        },
    })
    extra_point_options = [
        {"text": {"type": "plain_text", "text": "No extra"}, "value": "0"},
        *[{"text": {"type": "plain_text", "text": f"+{i} pt" if i == 1 else f"+{i} pts"}, "value": str(i)} for i in range(1, 9)],
    ]
    blocks.append({
        "type": "section",
        "block_id": "extra_points_block",
        "text": {"type": "mrkdwn", "text": "*Extra points* (optional reward):"},
        "accessory": {
            "type": "static_select",
            "action_id": "extra_points_select",
            "placeholder": {"type": "plain_text", "text": "0 (none)"},
            "options": extra_point_options,
        },
    })
    blocks.append({
        "type": "actions",
        "block_id": "review_actions",
        "elements": [
            {"type": "button", "text": {"type": "plain_text", "text": "Accept"}, "style": "primary", "action_id": "review_accept", "value": submission_id},
            {"type": "button", "text": {"type": "plain_text", "text": "Reject"}, "action_id": "review_reject", "value": f"{submission_id}|{queue_msg_ts}|{queue_ch_id}"},
        ],
    })
    return {
        "type": "modal",
        "callback_id": "review_modal",
        "title": {"type": "plain_text", "text": "Review Submission"},
        "private_metadata": f"{submission_id}|{queue_msg_ts}|{queue_ch_id}",
        "blocks": blocks,
    }


def challenge_select_callback(ack: Ack, body: dict):
    """Ack dropdown selection so Slack doesn't show 404. Value is stored in modal state for Accept."""
    ack()


def extra_points_select_callback(ack: Ack):
    """Ack extra points dropdown selection. Value is stored in modal state for Accept."""
    ack()


def open_review_modal_callback(ack: Ack, client: WebClient, body: dict, logger: Logger):
    """Open review modal when Review button is clicked. Fetches next PENDING from queue."""
    try:
        ack()
        user_id = body.get("user", {}).get("id", "")
        if not is_admin(user_id):
            return
        action = next((a for a in body.get("actions", []) if a.get("action_id") == "open_review_modal"), None)
        if not action:
            return
        trigger_id = body.get("trigger_id", "")
        queue_ws = get_queue_ws()
        queue_msg_ts, queue_ch_id = get_queue_ref(queue_ws)
        if not queue_msg_ts or not queue_ch_id:
            queue_msg_ts = body.get("message", {}).get("ts", "")
            queue_ch_id = body.get("channel", {}).get("id", "")

        loading_modal = {
            "type": "modal",
            "callback_id": "review_modal",
            "title": {"type": "plain_text", "text": "Review Submission"},
            "private_metadata": f"queue|{queue_msg_ts}",
            "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": "Loading…"}}],
        }
        resp = client.views_open(trigger_id=trigger_id, view=loading_modal)
        view_id = resp.get("view", {}).get("id") if resp else None
        if not view_id:
            return

        submissions_ws = get_submissions_ws()
        submission = get_next_pending_submission(submissions_ws)
        if not submission:
            client.views_update(view_id=view_id, view={"type": "modal", "title": loading_modal["title"], "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": "No submissions in queue."}}]})
            return
        submission_id = (submission.get("submission_id") or "").strip()
        full_modal = _build_review_modal(submission, submission_id, queue_msg_ts or "", queue_ch_id or "")
        client.views_update(view_id=view_id, hash=resp.get("view", {}).get("hash"), view=full_modal)
    except Exception as e:
        logger.exception("Open review modal failed: %s", e)


def review_accept_callback(ack: Ack, client: WebClient, body: dict, logger: Logger):
    """Handle Accept in review modal."""
    try:
        ack()
        user_id = body.get("user", {}).get("id", "")
        if not is_admin(user_id):
            return
        view = body.get("view", {})
        values = view.get("state", {}).get("values", {})
        challenge_block = values.get("challenge_block", {}).get("challenge_select", {})
        selected = challenge_block.get("selected_option")
        if not selected:
            client.views_update(
                view_id=view["id"],
                hash=view.get("hash"),
                view={**view, "blocks": view.get("blocks", [])},
            )
            return
        val = selected.get("value", "")
        if "|" in val:
            challenge_key, points_str = val.split("|", 1)
            points = int(points_str) if points_str else 0
        else:
            challenge_key, points = val, 0
        extra_points = 0
        extra_block = values.get("extra_points_block", {}).get("extra_points_select", {})
        extra_opt = extra_block.get("selected_option")
        if extra_opt:
            try:
                extra_points = int(extra_opt.get("value", "0") or "0")
            except (TypeError, ValueError):
                pass
        points = points + extra_points
        private = view.get("private_metadata", "")
        parts = private.split("|")
        submission_id = parts[0] if parts else ""
        queue_msg_ts = parts[1] if len(parts) > 1 else ""
        queue_ch_id = parts[2] if len(parts) > 2 else ""

        submissions_ws = get_submissions_ws()
        ledger_ws = get_ledger_ws()
        members_ws = get_members_ws()
        submission = get_submission_by_id(submissions_ws, submission_id)
        if not submission:
            return
        already_approved = (submission.get("status", "") or "").strip().lower() == "approved"

        team = submission.get("team", "").strip()
        reviewer_name = get_reviewer_display_name(client, members_ws, user_id)
        update_submission_status(submissions_ws, submission_id, "APPROVED", reviewer_name, challenge_key=challenge_key, points=points)

        if not already_approved:
            add_ledger_entry(ledger_ws, team, points, challenge_key, submission_id, reviewer_name)

        queue_ws = get_queue_ws()
        queue_msg_ts, queue_ch_id = update_queue_message(client, queue_ws, submissions_ws, REVIEW_CHANNEL_ID)
        if not queue_msg_ts or not queue_ch_id:
            queue_msg_ts = queue_msg_ts or (parts[1] if len(parts) > 1 else "")
            queue_ch_id = queue_ch_id or (parts[2] if len(parts) > 2 else "")

        if not already_approved and queue_msg_ts and queue_ch_id:
            ts_slack = _to_slack_ts(queue_msg_ts)
            if ts_slack:
                client.chat_postMessage(
                    channel=queue_ch_id,
                    thread_ts=ts_slack,
                    text=f"✅ *Approved* by <@{user_id}> – {points} pts added to *{team}*",
                )
        if not already_approved and CHALLENGE_CHANNEL_ID and (challenge_key or "").strip().upper().startswith("SUP-"):
            challenges_ws = get_challenges_ws()
            for c in get_challenges(challenges_ws):
                if (c.get("challenge_key") or "").strip() == challenge_key:
                    name = (c.get("challenge_name") or "").strip()
                    msg = f"<!channel> SURPRISE CHALLENGE COMPLETE THIS WEEK FOR {points} POINTS!!!! AND {name} SURPRISE!"
                    client.chat_postMessage(channel=CHALLENGE_CHANNEL_ID, text=msg)
                    break

        # Auto-advance to next pending submission in the same modal
        next_submission = get_next_pending_submission(submissions_ws)
        if not next_submission:
            client.views_update(
                view_id=view["id"],
                hash=view.get("hash"),
                view={
                    "type": "modal",
                    "title": {"type": "plain_text", "text": "Review Submission"},
                    "blocks": [
                        {
                            "type": "section",
                            "text": {"type": "mrkdwn", "text": "No more submissions in the queue."},
                        }
                    ],
                },
            )
            return

        next_submission_id = (next_submission.get("submission_id") or "").strip()
        next_modal = _build_review_modal(next_submission, next_submission_id, queue_msg_ts or "", queue_ch_id or "")
        client.views_update(view_id=view["id"], hash=view.get("hash"), view=next_modal)
    except Exception as e:
        logger.exception("Review accept failed: %s", e)


def review_reject_callback(ack: Ack, client: WebClient, body: dict, logger: Logger):
    """Handle Reject - open modal for rejection reason."""
    try:
        ack()
        user_id = body.get("user", {}).get("id", "")
        if not is_admin(user_id):
            return
        action = next((a for a in body.get("actions", []) if a.get("action_id") == "review_reject"), None)
        if not action:
            return
        value = action.get("value", "")
        # Value format: submission_id|queue_msg_ts|queue_ch_id (or legacy: submission_id|queue_msg_ts)
        parts = value.split("|")
        submission_id = parts[0] if parts else ""
        queue_msg_ts = parts[1] if len(parts) > 1 else ""
        queue_ch_id = parts[2] if len(parts) > 2 else ""
        private_metadata = f"{submission_id}|{queue_msg_ts}|{queue_ch_id}"

        # Replace current review modal with rejection reason modal (no stacked views)
        view = body.get("view", {})
        reject_view = {
            "type": "modal",
            "callback_id": "reject_reason_modal",
            "title": {"type": "plain_text", "text": "Reject Submission"},
            "private_metadata": private_metadata,
            "submit": {"type": "plain_text", "text": "Reject"},
            "blocks": [
                {
                    "type": "input",
                    "block_id": "reject_reason_block",
                    "label": {"type": "plain_text", "text": "Rejection reason (shown as comment under the submission)"},
                    "element": {"type": "plain_text_input", "action_id": "reject_reason_input", "multiline": True},
                },
            ],
        }
        client.views_update(view_id=view["id"], hash=view.get("hash"), view=reject_view)
    except Exception as e:
        logger.exception("Review reject failed: %s", e)
