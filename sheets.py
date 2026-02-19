"""Lazy-loaded sheet references for use across app and listeners."""

import os
import json
import logging

import gspread
from dotenv import load_dotenv
from google.oauth2.service_account import Credentials

_sheet = None


def _build_google_creds() -> Credentials:
    """Create Google service account credentials using env-based JSON or a file path."""
    SCOPES = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    service_account_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")

    creds = None
    if service_account_json:
        try:
            info = json.loads(service_account_json)
            creds = Credentials.from_service_account_info(info, scopes=SCOPES)
        except json.JSONDecodeError:
            logging.getLogger(__name__).error(
                "GOOGLE_SERVICE_ACCOUNT_JSON is set but contains invalid JSON."
            )

    if creds is None and creds_path and os.path.exists(creds_path):
        creds = Credentials.from_service_account_file(creds_path, scopes=SCOPES)

    if creds is None:
        raise RuntimeError(
            "Google service account credentials are not configured. "
            "Set GOOGLE_SERVICE_ACCOUNT_JSON to the JSON contents of your service account "
            "or provide a valid GOOGLE_APPLICATION_CREDENTIALS file path."
        )

    return creds


def get_sheet():
    """Get the gspread spreadsheet object (singleton)."""
    global _sheet
    if _sheet is None:
        load_dotenv()
        creds = _build_google_creds()
        client = gspread.authorize(creds)
        _sheet = client.open_by_key(os.environ["SPREADSHEET_ID"])
    return _sheet


def get_submissions_ws():
    return get_sheet().worksheet("Submissions")


def get_ledger_ws():
    return get_sheet().worksheet("Ledger")


def get_challenges_ws():
    return get_sheet().worksheet("Challenges")


def get_members_ws():
    return get_sheet().worksheet("Members")


def get_queue_ws():
    """Queue sheet: tracks single review message. Columns: message_ts, channel_id (row 2)."""
    try:
        return get_sheet().worksheet("Queue")
    except Exception:
        return get_sheet().add_worksheet(title="Queue", rows=10, cols=5)
