"""FastAPI adapter for the pure signed-review callback contract."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from fastapi import APIRouter, HTTPException, Request

from review_callback import (
    CallbackFailure,
    MAX_BODY_BYTES,
    ReviewCallbackHeaders,
    ReviewDatabase,
    process_review_callback,
)


_LOGGER = logging.getLogger(__name__)


def create_review_callback_router(
    connection_factory: Callable[[], ReviewDatabase],
    secret_path: Callable[[], Path],
    reviewer_id: Callable[[], str],
    clock: Callable[[], int],
    max_skew_seconds: Callable[[], int],
) -> APIRouter:
    """Create the SF04 HTTP route without coupling signature logic to FastAPI."""
    router = APIRouter()

    @router.post("/v1/reviews/decision")
    async def review_decision(request: Request):
        raw_body = bytearray()
        async for chunk in request.stream():
            raw_body.extend(chunk)
            if len(raw_body) > MAX_BODY_BYTES:
                raise HTTPException(413, "request_too_large")
        headers = ReviewCallbackHeaders(
            timestamp=request.headers.get("x-review-timestamp"),
            nonce=request.headers.get("x-review-nonce"),
            event_id=request.headers.get("x-review-event-id"),
            signature=request.headers.get("x-review-signature"),
        )
        try:
            result = process_review_callback(
                connection_factory=connection_factory,
                secret_path=secret_path(),
                reviewer_id=reviewer_id(),
                raw_body=bytes(raw_body),
                headers=headers,
                now=clock(),
                max_skew_seconds=max_skew_seconds(),
            )
        except CallbackFailure as exc:
            raise HTTPException(exc.status_code, exc.code) from None
        except Exception:
            _LOGGER.exception("Review callback transaction failed")
            raise HTTPException(503, "review_callback_unavailable") from None
        return {
            "review_id": result.review_id,
            "decision": result.decision,
            "state": result.state,
            "production_authorized": result.production_authorized,
            "youtube_upload": False,
        }

    return router
