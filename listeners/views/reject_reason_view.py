"""View handler for rejection reason modal - posts reply under submission."""

import os
from logging import Logger

from slack_bolt import Ack
from slack_sdk import WebClient

from sheets import get_submissions_ws, get_queue_ws, get_members_ws
from helpers import (
    get_submission_by_id,
    update_submission_status,
    update_queue_message,
    get_reviewer_display_name,
    _to_slack_ts,
    is_admin,
    get_next_pending_submission,
)
from listeners.actions.review_modal_action import _build_review_modal

REVIEW_CHANNEL_ID = os.environ.get("REVIEW_CHANNEL_ID", "")


def reject_reason_view_callback(ack: Ack, body: dict, client: WebClient, view: dict, logger: Logger):
    """On submit of rejection reason modal - update status, post reply under submission."""
    try:
        user_id = body.get("user", {}).get("id", "")
        if not is_admin(user_id):
            ack()
            return
        private = view.get("private_metadata", "")
        parts = private.split("|")
        submission_id = parts[0] if parts else ""
        queue_msg_ts = parts[1] if len(parts) > 1 else ""
        queue_ch_id = parts[2] if len(parts) > 2 else ""

        values = view.get("state", {}).get("values", {})
        reason_block = values.get("reject_reason_block", {}).get("reject_reason_input", {})
        rejection_message = (reason_block.get("value") or "").strip() or "_No reason provided_"

        submissions_ws = get_submissions_ws()
        members_ws = get_members_ws()
        submission_row = get_submission_by_id(submissions_ws, submission_id)
        already_rejected = (submission_row or {}).get("status", "").strip().lower() == "rejected"

        reviewer_name = get_reviewer_display_name(client, members_ws, user_id)
        update_submission_status(submissions_ws, submission_id, "REJECTED", reviewer_name)

        queue_ws = get_queue_ws()
        queue_msg_ts, queue_ch_id = update_queue_message(client, queue_ws, submissions_ws, REVIEW_CHANNEL_ID)
        if not queue_msg_ts or not queue_ch_id:
            queue_msg_ts = queue_msg_ts or (parts[1] if len(parts) > 1 else "")
            queue_ch_id = queue_ch_id or (parts[2] if len(parts) > 2 else "")

        if not already_rejected and queue_msg_ts and queue_ch_id and REVIEW_CHANNEL_ID:
            ts_slack = _to_slack_ts(queue_msg_ts)
            if ts_slack:
                client.chat_postMessage(
                    channel=REVIEW_CHANNEL_ID,
                    thread_ts=ts_slack,
                    text=f"AWKKK... *Rejected* by <@{user_id}>:\n\n{rejection_message}",
                )

        if not already_rejected and submission_row:
            orig = (submission_row.get("message_url") or "").strip()
            if "|" in orig:
                orig_channel, orig_ts = orig.split("|", 1)
                orig_ts_slack = _to_slack_ts(orig_ts)
                if orig_channel and orig_ts_slack:
                    client.chat_postMessage(
                        channel=orig_channel,
                        thread_ts=orig_ts_slack,
                        text=f"AWKKK... *Rejected* by <@{user_id}>: {rejection_message}",
                    )
        # Auto-advance: after rejecting, show the next pending submission (or "no more").
        next_submission = get_next_pending_submission(submissions_ws)
        if not next_submission:
            ack(
                response_action="update",
                view={
                    "type": "modal",
                    "title": {"type": "plain_text", "text": "Review Submission"},
                    "close": {"type": "plain_text", "text": "Close"},
                    "blocks": [
                        {
                            "type": "section",
                            "text": {"type": "mrkdwn", "text": "No more submissions in the queue."},
                        }
                    ],
                },
            )
            return

        next_id = (next_submission.get("submission_id") or "").strip()
        next_modal = _build_review_modal(next_submission, next_id, queue_msg_ts or "", queue_ch_id or "")
        ack(response_action="update", view=next_modal)
    except Exception as e:
        logger.exception("Reject reason view failed: %s", e)
