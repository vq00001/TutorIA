"""Bounded opaque cursors for stable (created_at, UUID) pagination."""

import base64
import json
from datetime import datetime, timezone
from uuid import UUID

from backend.api.common import ApiError


def encode_cursor(created_at: datetime, identifier: UUID) -> str:
    payload = json.dumps(
        {"at": created_at.astimezone(timezone.utc).isoformat(), "id": str(identifier)},
        separators=(",", ":"),
    ).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def decode_cursor(value: str | None):
    if value is None:
        return None, None
    try:
        if not 1 <= len(value) <= 512:
            raise ValueError("cursor size")
        payload = json.loads(
            base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
        )
        if not isinstance(payload, dict) or set(payload) != {"at", "id"}:
            raise ValueError("cursor fields")
        if not isinstance(payload["at"], str) or not isinstance(payload["id"], str):
            raise ValueError("cursor types")
        created_at = datetime.fromisoformat(payload["at"].replace("Z", "+00:00"))
        identifier = UUID(payload["id"])
        if created_at.tzinfo is None:
            raise ValueError("cursor timezone")
        return created_at.astimezone(timezone.utc), identifier
    except (ValueError, TypeError, KeyError, UnicodeError) as exc:
        raise ApiError("INVALID_CURSOR", 422) from exc
