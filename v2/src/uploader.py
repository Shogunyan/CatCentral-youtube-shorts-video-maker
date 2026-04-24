"""
Uploads the finished Short to YouTube via the Data API v3.

Shares the OAuth token with the parent project so both upload to the
same channel without requiring separate authentication.
"""

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_CATEGORY_PETS = "15"
_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
]


class YouTubeUploader:
    def __init__(self, config):
        self.config = config

    def upload(
        self,
        video_path: Path,
        title: str,
        description: str,
        tags: list,
    ) -> Optional[str]:
        """Upload video, return YouTube video ID on success or None."""
        try:
            youtube = self._build_service()
            if youtube is None:
                return None

            # Ensure Shorts signal and length compliance
            if "#shorts" not in title.lower():
                title = title + " #shorts"
            if len(title) > 100:
                title = title[:97] + "..."

            body = {
                "snippet": {
                    "title": title,
                    "description": description,
                    "tags": tags,
                    "categoryId": _CATEGORY_PETS,
                    "defaultLanguage": "en",
                    "defaultAudioLanguage": "en",
                },
                "status": {
                    "privacyStatus": "public",
                    "selfDeclaredMadeForKids": False,
                },
            }

            from googleapiclient.http import MediaFileUpload

            media = MediaFileUpload(
                str(video_path),
                mimetype="video/mp4",
                resumable=True,
                chunksize=10 * 1024 * 1024,
            )

            request = youtube.videos().insert(
                part=",".join(body.keys()),
                body=body,
                media_body=media,
            )

            response = None
            while response is None:
                status, response = request.next_chunk()
                if status:
                    logger.info(f"Upload: {int(status.progress() * 100)}%")

            video_id = response.get("id", "")
            logger.info(f"Uploaded: https://youtube.com/shorts/{video_id}")
            return video_id

        except Exception as e:
            logger.error(f"Upload error: {e}")
            return None

    def _build_service(self):
        """Build an authenticated YouTube service object."""
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
        from google_auth_oauthlib.flow import InstalledAppFlow

        token_path = self.config.youtube_token_path
        creds = None

        if token_path.exists():
            try:
                with open(token_path) as f:
                    data = json.load(f)
                creds = Credentials(
                    token=data.get("token"),
                    refresh_token=data.get("refresh_token"),
                    token_uri="https://oauth2.googleapis.com/token",
                    client_id=self.config.google_client_id,
                    client_secret=self.config.google_client_secret,
                    scopes=_SCOPES,
                )
            except Exception as e:
                logger.warning(f"Failed to load token: {e}")

        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                self._save_token(creds, token_path)
            except Exception as e:
                logger.warning(f"Token refresh failed: {e}")
                creds = None

        if not creds or not creds.valid:
            if not self.config.google_client_id or not self.config.google_client_secret:
                logger.error("YouTube OAuth credentials not configured in .env")
                return None
            client_config = {
                "installed": {
                    "client_id": self.config.google_client_id,
                    "client_secret": self.config.google_client_secret,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                }
            }
            flow = InstalledAppFlow.from_client_config(client_config, _SCOPES)
            creds = flow.run_local_server(port=0)
            self._save_token(creds, token_path)

        return build("youtube", "v3", credentials=creds)

    @staticmethod
    def _save_token(creds, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(
                {
                    "token": creds.token,
                    "refresh_token": creds.refresh_token,
                    "token_uri": creds.token_uri,
                    "client_id": creds.client_id,
                    "client_secret": creds.client_secret,
                    "scopes": list(creds.scopes or []),
                },
                f,
                indent=2,
            )
