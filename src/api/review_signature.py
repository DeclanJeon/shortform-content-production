"""Signed review callback verification and replay-store contract.

Canonical signed bytes are UTF-8/ASCII header fields followed by the exact body:
``v1\n{timestamp}\n{nonce}\n{event_id}\n{decision}\n`` + raw_body.
The timestamp is its canonical decimal Unix-seconds representation. The signature
header is ``sha256=`` followed by 64 lowercase hexadecimal characters.
"""
from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass
from typing import Protocol


_DECISIONS = frozenset({"approve", "reject", "revise"})
_NONCE_RE = re.compile(r"[A-Za-z0-9_-]{16,128}\Z", re.ASCII)
_EVENT_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,127}\Z", re.ASCII)
_TIMESTAMP_RE = re.compile(r"(?:0|[1-9][0-9]{0,11})\Z", re.ASCII)
_SIGNATURE_RE = re.compile(r"sha256=[0-9a-f]{64}\Z", re.ASCII)


class InvalidCallback(ValueError):
    """The callback is malformed, unauthenticated, or outside its time window."""


class ReplayDetected(ValueError):
    """The event ID or nonce has already been claimed."""


@dataclass(frozen=True)
class VerifiedReview:
    timestamp: int
    nonce: str
    event_id: str
    decision: str
    raw_body: bytes
    payload_sha256: str


class ReplayStore(Protocol):
    def claim(self, event: VerifiedReview) -> bool:
        """Atomically persist the event, returning false on any unique conflict."""


def canonical_message(
    timestamp: str, nonce: str, event_id: str, decision: str, raw_body: bytes
) -> bytes:
    """Build the versioned signing message; body bytes are never decoded or normalized."""
    return (
        f"v1\n{timestamp}\n{nonce}\n{event_id}\n{decision}\n".encode("ascii")
        + raw_body
    )


def verify_signature(
    *,
    raw_body: bytes,
    timestamp: str,
    nonce: str,
    event_id: str,
    decision: str,
    signature: str,
    secret: bytes,
    now: int,
    max_skew_seconds: int = 300,
) -> VerifiedReview:
    """Validate callback fields and HMAC-SHA256 without parsing the request body."""
    if not isinstance(raw_body, bytes):
        raise InvalidCallback("invalid_body")
    if not isinstance(secret, bytes) or not secret:
        raise InvalidCallback("invalid_secret")
    if (
        not isinstance(max_skew_seconds, int)
        or isinstance(max_skew_seconds, bool)
        or max_skew_seconds < 0
    ):
        raise InvalidCallback("invalid_skew_configuration")
    if not isinstance(now, int) or isinstance(now, bool) or now < 0:
        raise InvalidCallback("invalid_clock")
    if not isinstance(timestamp, str) or not _TIMESTAMP_RE.fullmatch(timestamp):
        raise InvalidCallback("invalid_timestamp")
    if not isinstance(nonce, str) or not _NONCE_RE.fullmatch(nonce):
        raise InvalidCallback("invalid_nonce")
    if not isinstance(event_id, str) or not _EVENT_ID_RE.fullmatch(event_id):
        raise InvalidCallback("invalid_event_id")
    if not isinstance(decision, str) or decision not in _DECISIONS:
        raise InvalidCallback("invalid_decision")
    if not isinstance(signature, str) or not _SIGNATURE_RE.fullmatch(signature):
        raise InvalidCallback("invalid_signature")

    timestamp_value = int(timestamp)
    if abs(now - timestamp_value) > max_skew_seconds:
        raise InvalidCallback("expired_signature")

    expected = hmac.new(
        secret,
        canonical_message(timestamp, nonce, event_id, decision, raw_body),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(signature[7:], expected):
        raise InvalidCallback("invalid_signature")

    return VerifiedReview(
        timestamp=timestamp_value,
        nonce=nonce,
        event_id=event_id,
        decision=decision,
        raw_body=raw_body,
        payload_sha256=hashlib.sha256(raw_body).hexdigest(),
    )


def verify_and_claim(
    *,
    replay_store: ReplayStore,
    raw_body: bytes,
    timestamp: str,
    nonce: str,
    event_id: str,
    decision: str,
    signature: str,
    secret: bytes,
    now: int,
    max_skew_seconds: int = 300,
) -> VerifiedReview:
    """Verify first, then atomically claim nonce and event ID in the replay store."""
    event = verify_signature(
        raw_body=raw_body,
        timestamp=timestamp,
        nonce=nonce,
        event_id=event_id,
        decision=decision,
        signature=signature,
        secret=secret,
        now=now,
        max_skew_seconds=max_skew_seconds,
    )
    if not replay_store.claim(event):
        raise ReplayDetected("duplicate_callback")
    return event
