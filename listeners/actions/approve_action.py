"""Action handler for approving submissions."""

from logging import Logger

from slack_bolt import Ack
from slack_sdk import WebClient

from sheets import get_submissions_ws, get_ledger_ws, get_members_ws
from helpers import (
    get_submission_by_id,
    update_submission_status,
    add_ledger_entry,
    get_reviewer_display_name,
    is_admin,
)


def approve_submission_callback(ack: Ack, client: WebClient, body: dict, logger: Logger):
    try:
        ack()
        user_id = body.get("user", {}).get("id", "")
        if not is_admin(user_id):
            client.chat_postEphemeral(
                channel=body["channel"]["id"],
                user=user_id,
                text="Only admins can approve submissions.",
            )
            return

        action = next((a for a in body.get("actions", []) if a.get("action_id") == "approve_submission"), None)
        if not action:
            return
        submission_id = action.get("value", "")

        submissions_ws = get_submissions_ws()
        ledger_ws = get_ledger_ws()
        members_ws = get_members_ws()
        submission = get_submission_by_id(submissions_ws, submission_id)
        if not submission:
            client.chat_postEphemeral(
                channel=body["channel"]["id"],
                user=user_id,
                text=f"Submission {submission_id} not found.",
            )
            return

        team = submission.get("team", "").strip()
        challenge_key = submission.get("challenge_key", "").strip()
        try:
            points = int(submission.get("points", 0) or 0)
        except (TypeError, ValueError):
            points = 0

        reviewer_name = get_reviewer_display_name(client, members_ws, user_id)
        if not update_submission_status(submissions_ws, submission_id, "APPROVED", reviewer_name):
            client.chat_postEphemeral(
                channel=body["channel"]["id"],
                user=user_id,
                text="Failed to update submission status.",
            )
            return

        add_ledger_entry(ledger_ws, team, points, challenge_key, submission_id, reviewer_name)

        # Update the message to show approved (remove button)
        ts = body.get("message", {}).get("ts")
        channel_id = body["channel"]["id"]
        blocks = [b for b in body.get("message", {}).get("blocks", []) if b.get("type") != "actions"]
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"✅ *Approved* by <@{user_id}> – {points} pts added to *{team}*"},
        })
        try:
            client.chat_update(channel=channel_id, ts=ts, blocks=blocks)
        except Exception as update_err:
            logger.warning("Could not update message, posting reply: %s", update_err)
            client.chat_postMessage(
                channel=channel_id,
                thread_ts=ts,
                text=f" *Approved* by <@{user_id}> – {points} pts added to *{team}*",
            )
    except Exception as e:
        logger.exception("Approve submission failed")
