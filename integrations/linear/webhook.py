"""Verify and interpret Linear webhook deliveries. Pure functions, no I/O."""

import hashlib
import hmac
import time

# Linear recommends rejecting deliveries older than about a minute (replay guard).
MAX_AGE_MS = 60_000


def signature_is_valid(raw_body: bytes, signature: str | None, secret: str) -> bool:
    if not signature or not secret:
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def is_fresh(payload: dict, now_ms: int | None = None) -> bool:
    sent_ms = payload.get("webhookTimestamp")
    if not isinstance(sent_ms, int):
        return False
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    return abs(now_ms - sent_ms) <= MAX_AGE_MS


def added_label_ids(payload: dict) -> list[str]:
    """Label IDs this event added to an issue; empty when no label was added."""
    if payload.get("type") != "Issue":
        return []
    data = payload.get("data") or {}
    current = set(data.get("labelIds") or [])
    action = payload.get("action")
    if action == "create":
        return sorted(current)
    if action == "update":
        updated_from = payload.get("updatedFrom") or {}
        if "labelIds" not in updated_from:
            return []
        return sorted(current - set(updated_from["labelIds"] or []))
    return []
