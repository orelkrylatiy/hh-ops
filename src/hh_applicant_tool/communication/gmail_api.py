"""Send vacancy mail through an existing local Gmail OAuth installation."""

from __future__ import annotations

import base64
import json
import time
from email.message import EmailMessage
from pathlib import Path

import requests


class GmailAPISender:
    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory).expanduser()
        self.credentials_file = self.directory / "credentials.json"
        self.token_file = self.directory / "token.json"
        self.config_file = self.directory / "config.json"
        for path in (self.credentials_file, self.token_file, self.config_file):
            if not path.is_file():
                raise ValueError(f"Gmail API configuration is missing: {path}")
        self.sender = str(json.loads(self.config_file.read_text(encoding="utf-8"))["email"])

    def _access_token(self) -> str:
        token = json.loads(self.token_file.read_text(encoding="utf-8"))
        if time.time() < token.get("expires_at", 0) - 60:
            return str(token["access_token"])
        credentials = json.loads(self.credentials_file.read_text(encoding="utf-8"))
        client = credentials.get("installed") or credentials.get("web")
        if not client or not token.get("refresh_token"):
            raise ValueError("Gmail OAuth refresh credentials are missing")
        response = requests.post(
            "https://oauth2.googleapis.com/token",
            data={
                "refresh_token": token["refresh_token"],
                "client_id": client["client_id"],
                "client_secret": client["client_secret"],
                "grant_type": "refresh_token",
            },
            timeout=30,
        )
        response.raise_for_status()
        refreshed = response.json()
        token.update(refreshed)
        token["expires_at"] = time.time() + refreshed.get("expires_in", 3600)
        self.token_file.write_text(json.dumps(token), encoding="utf-8")
        return str(token["access_token"])

    def profile_email(self) -> str:
        response = requests.get(
            "https://gmail.googleapis.com/gmail/v1/users/me/profile",
            headers={"Authorization": f"Bearer {self._access_token()}"},
            timeout=30,
        )
        response.raise_for_status()
        email = str(response.json()["emailAddress"])
        if email.casefold() != self.sender.casefold():
            raise ValueError("Gmail OAuth account does not match the configured sender")
        return email

    def send(self, to: str, subject: str, body: str) -> str:
        message = EmailMessage()
        message["From"] = self.sender
        message["To"] = to
        message["Subject"] = subject
        message.set_content(body)
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
        response = requests.post(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
            headers={"Authorization": f"Bearer {self._access_token()}"},
            json={"raw": raw},
            timeout=30,
        )
        response.raise_for_status()
        return str(response.json()["id"])
