"""Ephemeral match-photo sessions for the Gesture Football laptop host."""

from __future__ import annotations

import hmac
import secrets
import time
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class MatchPhoto:
    id: str
    label: str
    kick: int | None
    result: str | None
    score: str
    created_at: int
    filename: str

    def public(self) -> dict:
        data = asdict(self)
        data.pop("filename")
        return data


class PhotoBooth:
    """Owns one short-lived QR session and its unguessable photo files."""

    def __init__(self, root: Path, ttl_seconds: int = 900, max_photos: int = 24):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.ttl_seconds = ttl_seconds
        self.max_photos = max_photos
        self.photos: list[MatchPhoto] = []
        self._remove_orphaned_files()
        self._rotate()

    def _rotate(self) -> None:
        self.session_id = secrets.token_urlsafe(10)
        self.join_token = secrets.token_urlsafe(32)
        self.capture_token = secrets.token_urlsafe(32)
        self.created_at = int(time.time())
        self.expires_at = self.created_at + self.ttl_seconds

    def _remove_orphaned_files(self) -> None:
        # Tokens are intentionally memory-only. After a restart no prior
        # gallery can authenticate, so retaining its photos would serve no use.
        for path in self.root.glob("*.jpg"):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    @staticmethod
    def _matches(expected: str, supplied: str | None) -> bool:
        return bool(supplied) and hmac.compare_digest(expected, supplied)

    def validate_join(self, token: str | None) -> bool:
        self.cleanup()
        return self._matches(self.join_token, token)

    def validate_capture(self, token: str | None) -> bool:
        self.cleanup()
        return self._matches(self.capture_token, token)

    def snapshot(self) -> dict:
        self.cleanup()
        return {
            "sessionId": self.session_id,
            "expiresAt": self.expires_at,
            "photoCount": len(self.photos),
            "photos": [photo.public() for photo in self.photos],
        }

    def add_photo(
        self,
        image: bytes,
        *,
        label: str,
        kick: int | None,
        result: str | None,
        score: str,
    ) -> MatchPhoto:
        self.cleanup()
        if not image.startswith(b"\xff\xd8\xff"):
            raise ValueError("Only JPEG camera captures are accepted")
        photo_id = secrets.token_urlsafe(18)
        filename = f"{self.session_id}-{photo_id}.jpg"
        path = self.root / filename
        path.write_bytes(image)
        photo = MatchPhoto(
            id=photo_id,
            label=label[:80],
            kick=kick,
            result=result,
            score=score[:20],
            created_at=int(time.time()),
            filename=filename,
        )
        self.photos.append(photo)
        while len(self.photos) > self.max_photos:
            removed = self.photos.pop(0)
            (self.root / removed.filename).unlink(missing_ok=True)
        return photo

    def find_photo(self, photo_id: str) -> tuple[MatchPhoto, Path] | None:
        self.cleanup()
        photo = next((item for item in self.photos if item.id == photo_id), None)
        if not photo:
            return None
        path = self.root / photo.filename
        return (photo, path) if path.is_file() else None

    def cleanup(self, now: int | None = None) -> bool:
        now = int(time.time()) if now is None else now
        if now < self.expires_at:
            return False
        for photo in self.photos:
            (self.root / photo.filename).unlink(missing_ok=True)
        self.photos.clear()
        self._rotate()
        return True
