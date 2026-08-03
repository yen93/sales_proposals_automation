"""One-time script: completes the Google OAuth consent flow and persists the
refresh token into .env so the pipeline can run unattended afterwards.

Usage:
    python oauth_setup.py

Requires GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET to already be set in .env,
from a Google Cloud OAuth client with Gmail, Drive, and Slides API access
enabled. Opens a browser window for you to grant consent as the mailbox/drive
owner (julienne@myadventuregroup.com.au).
"""

import re
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

import config

BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"


def _write_refresh_token(refresh_token: str) -> None:
    if not ENV_PATH.exists():
        ENV_PATH.write_text(f"GOOGLE_REFRESH_TOKEN={refresh_token}\n", encoding="utf-8")
        return

    text = ENV_PATH.read_text(encoding="utf-8")
    if re.search(r"^GOOGLE_REFRESH_TOKEN=.*$", text, flags=re.MULTILINE):
        text = re.sub(
            r"^GOOGLE_REFRESH_TOKEN=.*$",
            f"GOOGLE_REFRESH_TOKEN={refresh_token}",
            text,
            flags=re.MULTILINE,
        )
    else:
        text = text.rstrip("\n") + f"\nGOOGLE_REFRESH_TOKEN={refresh_token}\n"
    ENV_PATH.write_text(text, encoding="utf-8")


def main() -> None:
    config.require("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET")

    client_config = {
        "installed": {
            "client_id": config.GOOGLE_CLIENT_ID,
            "client_secret": config.GOOGLE_CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }

    flow = InstalledAppFlow.from_client_config(client_config, scopes=config.GOOGLE_SCOPES)
    # access_type=offline + prompt=consent guarantees a refresh_token is returned
    creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")

    if not creds.refresh_token:
        raise RuntimeError(
            "No refresh token returned. Revoke prior app access at "
            "https://myaccount.google.com/permissions and rerun this script."
        )

    _write_refresh_token(creds.refresh_token)
    print("Saved GOOGLE_REFRESH_TOKEN to .env — the pipeline can now run unattended.")


if __name__ == "__main__":
    main()
