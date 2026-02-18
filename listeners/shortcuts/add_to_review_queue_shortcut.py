"""Message shortcut: Add this message to the review queue (for submissions that weren't marked)."""

import os
import re
from logging import Logger

from slack_bolt import Ack
from slack_sdk import WebClient

from helpers import is_admin, submission_exists_for_message
from sheets import get_submissions_ws

CHALLENGE_CHANNEL_ID = os.environ.get("CHALLENGE_CHANNEL_ID", "")


def _get_app_refs():
    """Lazy import to avoid circular dependency."""
    import app as _app
    return _app._process_challenge_message, _app._update_queue_message


def add_to_review_queue_callback(
    ack: Ack,
    client: WebClient,
    body: dict,
    logger: Logger,
):
    """Add the selected message to the review queue if it's an unmarked submission."""
    try:
        ack()
        user_id = body.get("user", {}).get("id", "")
        if not is_admin(user_id):
            client.chat_postEphemeral(
                channel=body["channel"]["id"],
                user=user_id,
                text="Only admins can add messages to the review queue.",
            )
            return

        channel_id = body.get("channel", {}).get("id", "")
        if channel_id != CHALLENGE_CHANNEL_ID:
            client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text="Use this shortcut on messages in the challenge channel.",
            )
            return

        message = body.get("message", {})
        msg_ts = message.get("ts", "")
        text_raw = (message.get("text") or "").strip()
        files = message.get("files") or []
        msg_user_id = message.get("user", "")

        if not msg_ts or not msg_user_id:
            client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text="Could not read the message.",
            )
            return

        if not re.match(r"(?i)challenges?\s*", text_raw):
            client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text="This message doesn't look like a challenge submission (should start with 'challenge').",
            )
            return

        if not files:
            client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text="This message has no attachments. Challenge submissions need a photo or video.",
            )
            return

        submissions_ws = get_submissions_ws()
        if submission_exists_for_message(submissions_ws, msg_ts):
            client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text="This submission is already in the review queue.",
            )
            return

        process_msg, update_queue = _get_app_refs()
        process_msg(
            client, channel_id, msg_ts, msg_user_id, text_raw, files, logger,
        )
        update_queue(client)

        client.chat_postEphemeral(
            channel=channel_id,
            user=user_id,
            text="Added to review queue.",
        )
    except Exception as e:
        logger.exception("Add to review queue failed: %s", e)
        try:
            client.chat_postEphemeral(
                channel=body.get("channel", {}).get("id", ""),
                user=body.get("user", {}).get("id", ""),
                text=f"Failed to add: {e}",
            )
        except Exception:
            pass
