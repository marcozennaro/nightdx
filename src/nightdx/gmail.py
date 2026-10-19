"""Create Gmail drafts (with the recording attached) through the Gmail API.

One-time setup (see README): create a Google Cloud OAuth client of type
"Desktop app" with the Gmail API enabled, save its JSON as
gmail_credentials.json in the NightDX data folder, then run `nightdx gmail-setup` and approve
in the browser. Only the gmail.compose scope is requested; NightDX only
ever creates drafts - you send them yourself from Gmail.
"""

from __future__ import annotations

import io
import urllib.parse
from email.message import EmailMessage
from pathlib import Path

from . import config

SCOPES = ["https://www.googleapis.com/auth/gmail.compose"]
last_draft_id = [""]


def credentials_path() -> Path:
    return config.HOME / "gmail_credentials.json"


def token_path() -> Path:
    return config.HOME / "gmail_token.json"


class NotSetUp(RuntimeError):
    pass


def available() -> bool:
    try:
        import google_auth_oauthlib  # noqa: F401
        import googleapiclient  # noqa: F401
        return True
    except ImportError:
        return False


def _creds(interactive: bool, account: str = ""):
    if not available():
        raise NotSetUp("Gmail support is not installed - run: nightdx setup (or install the "
                       "'gmail' extra)")
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    creds = None
    if token_path().exists():
        creds = Credentials.from_authorized_user_file(str(token_path()), SCOPES)
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    elif interactive:
        if not credentials_path().exists():
            raise NotSetUp(f"Missing {credentials_path()} - see README, 'Gmail drafts'.")
        from google_auth_oauthlib.flow import InstalledAppFlow
        flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path()), SCOPES)
        kw = {"login_hint": account} if account else {}
        creds = flow.run_local_server(port=0, prompt="consent", **kw)
    else:
        raise NotSetUp("Gmail is not connected yet - run: uv run nightdx gmail-setup")
    token_path().write_text(creds.to_json())
    token_path().chmod(0o600)
    return creds


def setup(account: str = "") -> str:
    """Interactive OAuth consent; returns the connected address."""
    from googleapiclient.discovery import build
    svc = build("gmail", "v1", credentials=_creds(True, account), cache_discovery=False)
    return svc.users().getProfile(userId="me").execute()["emailAddress"]


def connected() -> bool:
    return token_path().exists()


def create_draft(to: str, subject: str, text: str, html: str, attachment: Path | None,
                 account: str = "") -> str:
    """Create the draft and return a URL that opens it in Gmail."""
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaIoBaseUpload
    msg = EmailMessage()
    if to:
        msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    if attachment and attachment.exists():
        sub = {"flac": "flac", "wav": "wav", "mp3": "mpeg"}.get(attachment.suffix.lstrip(".").lower(),
                                                                 "octet-stream")
        msg.add_attachment(attachment.read_bytes(), maintype="audio", subtype=sub,
                           filename=attachment.name)
    svc = build("gmail", "v1", credentials=_creds(False), cache_discovery=False)
    media = MediaIoBaseUpload(io.BytesIO(msg.as_bytes()), mimetype="message/rfc822",
                              resumable=True)
    d = svc.users().drafts().create(userId="me", body={}, media_body=media).execute()
    last_draft_id[0] = d["id"]
    who = account or svc.users().getProfile(userId="me").execute()["emailAddress"]
    # /mail/u/<address>/ gives Gmail's "Temporary Error (404)"; authuser= selects the account
    return (f"https://mail.google.com/mail/?authuser={urllib.parse.quote(who)}"
            f"#drafts?compose={d['message']['id']}")


def draft_exists(draft_id: str) -> bool:
    from googleapiclient.discovery import build
    try:
        svc = build("gmail", "v1", credentials=_creds(False), cache_discovery=False)
        svc.users().drafts().get(userId="me", id=draft_id, format="minimal").execute()
        return True
    except Exception:
        return False


def draft_status(draft_id: str) -> str:
    """'sent' (Gmail keeps the id and labels it SENT), 'draft', or 'gone' (deleted unsent)."""
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    svc = build("gmail", "v1", credentials=_creds(False), cache_discovery=False)
    try:
        d = svc.users().drafts().get(userId="me", id=draft_id, format="minimal").execute()
    except HttpError as e:
        if e.resp.status == 404:
            return "gone"
        raise
    labels = d["message"].get("labelIds") or []
    return "sent" if "SENT" in labels else "draft"
