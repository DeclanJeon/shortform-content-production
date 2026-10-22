"""Authenticated, plan-bound production-review callback."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from types import TracebackType
from typing import Callable, Mapping, Protocol, Sequence, assert_never

from review_signature import InvalidCallback, VerifiedReview, verify_signature


MAX_BODY_BYTES = 16 * 1024
_CREATIVE_EVENT = "creative_production"
_MEDIA_EVENT = "creative_plan_ready"
_PENDING_REVIEW = "AWAITING_PRODUCTION_APPROVAL"
_REVIEW_ID = re.compile(r"production:([1-9][0-9]*):([0-9a-f]{64})\Z")
_REVIEWER_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@/-]{0,99}\Z")
_DECISIONS = ("approve", "reject", "revise")

type JsonValue = str | int | float | bool | None | list[JsonValue] | dict[str, JsonValue]


class DatabaseResult(Protocol):
    def fetchone(self) -> Mapping[str, JsonValue] | None: ...


class ReviewDatabase(Protocol):
    def __enter__(self) -> ReviewDatabase: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None: ...

    def execute(
        self, query: str, params: Sequence[JsonValue]
    ) -> DatabaseResult: ...


@dataclass(frozen=True, slots=True)
class ReviewCallbackHeaders:
    timestamp: str | None
    nonce: str | None
    event_id: str | None
    signature: str | None


@dataclass(frozen=True, slots=True)
class ReviewCallbackOutcome:
    review_id: str
    decision: str
    state: str
    production_authorized: bool
    youtube_upload: bool = False


@dataclass(frozen=True, slots=True)
class CallbackFailure(Exception):
    status_code: int
    code: str

    def __str__(self) -> str:
        return self.code


class ReviewDecision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    REVISE = "revise"


class DuplicateJsonKey(ValueError):
    """A signed review body contains a duplicate JSON object key."""


def _unique_object(pairs: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateJsonKey
        result[key] = value
    return result


def _verified_decision(
    raw_body: bytes,
    headers: ReviewCallbackHeaders,
    secret: bytes,
    now: int,
    max_skew_seconds: int,
) -> VerifiedReview:
    matches: list[VerifiedReview] = []
    for decision in _DECISIONS:
        try:
            matches.append(
                verify_signature(
                    raw_body=raw_body,
                    timestamp=headers.timestamp or "",
                    nonce=headers.nonce or "",
                    event_id=headers.event_id or "",
                    decision=decision,
                    signature=headers.signature or "",
                    secret=secret,
                    now=now,
                    max_skew_seconds=max_skew_seconds,
                )
            )
        except InvalidCallback:
            continue
    if len(matches) != 1:
        raise CallbackFailure(400, "invalid_callback")
    return matches[0]


def _payload(value: JsonValue) -> Mapping[str, JsonValue] | None:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def has_active_production_authorization(
    db: ReviewDatabase, story_id: str
) -> bool:
    """Require the approved plan to remain the active media handoff."""
    row = db.execute(
        """SELECT id FROM outbox
           WHERE event_type = %s AND aggregate_id = %s
             AND status IN ('PENDING', 'MEDIA_RUNNING', 'PRIVATE_VIDEO_READY')
             AND payload->>'production_authorized' = 'true'
             AND payload->>'publication_authorized' = 'false'
             AND (payload->>'plan_sha256') =
                 (payload->'production_approval'->>'plan_sha256')
           LIMIT 1""",
        (_MEDIA_EVENT, story_id),
    ).fetchone()
    return row is not None


def process_review_callback(
    *,
    connection_factory: Callable[[], ReviewDatabase],
    secret_path: Path,
    reviewer_id: str,
    raw_body: bytes,
    headers: ReviewCallbackHeaders,
    now: int,
    max_skew_seconds: int = 300,
) -> ReviewCallbackOutcome:
    """Verify and atomically apply one decision to its exact production plan."""
    if len(raw_body) > MAX_BODY_BYTES:
        raise CallbackFailure(413, "request_too_large")
    if (
        not _REVIEWER_ID.fullmatch(reviewer_id)
        or isinstance(max_skew_seconds, bool)
        or not isinstance(max_skew_seconds, int)
        or max_skew_seconds < 0
    ):
        raise CallbackFailure(503, "review_callback_unavailable")
    try:
        secret = secret_path.read_bytes()
    except OSError:
        raise CallbackFailure(503, "review_callback_unavailable") from None
    if not secret:
        raise CallbackFailure(503, "review_callback_unavailable")

    verified = _verified_decision(
        raw_body, headers, secret, now, max_skew_seconds
    )
    try:
        body = json.loads(raw_body, object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError, DuplicateJsonKey):
        raise CallbackFailure(400, "invalid_callback") from None
    if (
        not isinstance(body, dict)
        or set(body) != {"review_id", "decision", "event_id"}
        or not isinstance(body["review_id"], str)
        or body["decision"] != verified.decision
        or body["event_id"] != verified.event_id
    ):
        raise CallbackFailure(422, "invalid_review_decision")

    review_id = body["review_id"]
    review_match = _REVIEW_ID.fullmatch(review_id)
    if review_match is None:
        raise CallbackFailure(422, "invalid_review_decision")
    target_id = int(review_match[1])
    target_sha256 = review_match[2]

    match verified.decision:
        case ReviewDecision.APPROVE:
            state = "PRODUCTION_APPROVED"
            outbox_state = "PENDING"
            story_state = state
            production_authorized = True
        case ReviewDecision.REJECT:
            state = "PRODUCTION_REJECTED"
            outbox_state = "REJECTED"
            story_state = state
            production_authorized = False
        case ReviewDecision.REVISE:
            state = "PRODUCTION_REVISION_REQUESTED"
            outbox_state = "REVISION_REQUESTED"
            story_state = state
            production_authorized = False
        case unreachable:
            assert_never(unreachable)

    metadata = {
        "review_id": review_id,
        "event_id": verified.event_id,
        "nonce": verified.nonce,
        "signature_timestamp": verified.timestamp,
        "payload_sha256": verified.payload_sha256,
        "plan_sha256": target_sha256,
        "reviewer_id": reviewer_id,
        "decision": verified.decision,
    }
    with connection_factory() as db:
        claimed = db.execute(
            """INSERT INTO review_callback_events
               (event_id, nonce, review_id, decision, signature_timestamp, payload_sha256)
               VALUES (%s, %s, %s, %s, %s, %s)
               ON CONFLICT DO NOTHING RETURNING event_id""",
            (
                verified.event_id,
                verified.nonce,
                review_id,
                verified.decision,
                verified.timestamp,
                verified.payload_sha256,
            ),
        ).fetchone()
        if claimed is None:
            raise CallbackFailure(409, "duplicate_callback")

        child = db.execute(
            """SELECT id, event_type, aggregate_id, status, payload FROM outbox
               WHERE id = %s FOR UPDATE""",
            (target_id,),
        ).fetchone()
        if child is None:
            raise CallbackFailure(404, "review_not_found")
        child_payload = _payload(child["payload"])
        if (
            child["event_type"] != _MEDIA_EVENT
            or child["status"] != _PENDING_REVIEW
            or child_payload is None
            or child_payload.get("story_id") != child["aggregate_id"]
            or child_payload.get("plan_sha256") != target_sha256
            or child_payload.get("review_id") != review_id
            or child_payload.get("publication_authorized") is not False
        ):
            raise CallbackFailure(409, "review_state_conflict")

        parent_id = child_payload.get("task_id")
        if not isinstance(parent_id, str) or not parent_id.isdigit():
            raise CallbackFailure(409, "review_state_conflict")
        parent = db.execute(
            """SELECT id, event_type, status, payload FROM outbox
               WHERE id = %s FOR UPDATE""",
            (int(parent_id),),
        ).fetchone()
        if parent is None:
            raise CallbackFailure(409, "review_state_conflict")
        parent_payload = _payload(parent["payload"])
        if (
            parent["event_type"] != _CREATIVE_EVENT
            or parent["status"] != "PLAN_READY"
            or parent_payload is None
            or parent_payload.get("story_id") != child["aggregate_id"]
            or parent_payload.get("run_id") != child_payload.get("run_id")
            or parent_payload.get("plan_sha256") != target_sha256
        ):
            raise CallbackFailure(409, "review_state_conflict")

        story = db.execute(
            """SELECT id, status FROM stories WHERE id = %s FOR UPDATE""",
            (child["aggregate_id"],),
        ).fetchone()
        if story is None:
            raise CallbackFailure(404, "review_not_found")
        if story["status"] != "CREATIVE_PLAN_READY":
            raise CallbackFailure(409, "review_state_conflict")

        changed_story = db.execute(
            """UPDATE stories SET status = %s
               WHERE id = %s AND status = 'CREATIVE_PLAN_READY'
               RETURNING id""",
            (story_state, child["aggregate_id"]),
        ).fetchone()
        if changed_story is None:
            raise CallbackFailure(409, "review_state_conflict")

        db.execute(
            """INSERT INTO approvals (story_id, stage, decision, reviewer, evidence)
               VALUES (%s, 'production', %s, %s, %s)""",
            (
                child["aggregate_id"],
                verified.decision,
                reviewer_id,
                json.dumps(metadata),
            ),
        )
        changed_child = db.execute(
            """UPDATE outbox SET status = %s, payload = payload || %s::jsonb
               WHERE id = %s AND status = %s RETURNING id""",
            (
                outbox_state,
                json.dumps(
                    {
                        "production_authorized": production_authorized,
                        "production_approval": metadata,
                        "publication_authorized": False,
                    }
                ),
                target_id,
                _PENDING_REVIEW,
            ),
        ).fetchone()
        if changed_child is None:
            raise CallbackFailure(409, "review_state_conflict")

    return ReviewCallbackOutcome(
        review_id=review_id,
        decision=verified.decision,
        state=state,
        production_authorized=production_authorized,
    )
