"""Builds authenticated Gmail/Drive/Slides service clients from the stored refresh token."""

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

import config


def _load_credentials() -> Credentials:
    config.require("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN")

    creds = Credentials(
        token=None,
        refresh_token=config.GOOGLE_REFRESH_TOKEN,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=config.GOOGLE_CLIENT_ID,
        client_secret=config.GOOGLE_CLIENT_SECRET,
        scopes=config.GOOGLE_SCOPES,
    )
    creds.refresh(Request())
    return creds


class GoogleClients:
    """Lazily-built Gmail/Drive/Slides API clients sharing one refreshed credential."""

    def __init__(self):
        self._creds = _load_credentials()
        self._gmail = None
        self._drive = None
        self._slides = None

    @property
    def gmail(self):
        if self._gmail is None:
            self._gmail = build("gmail", "v1", credentials=self._creds)
        return self._gmail

    @property
    def drive(self):
        if self._drive is None:
            self._drive = build("drive", "v3", credentials=self._creds)
        return self._drive

    @property
    def slides(self):
        if self._slides is None:
            self._slides = build("slides", "v1", credentials=self._creds)
        return self._slides
