from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
from datetime import date, datetime, time
from typing import Any, Callable
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Header, HTTPException, Request

SHEET_ID = os.environ.get('SOURCE_SHEET_ID', 'CONFIGURE_SOURCE_SHEET_ID')
KST = ZoneInfo("Asia/Seoul")
DASHBOARD_HEADERS = [
    "수집일", "순위", "분류", "이슈 제목", "커뮤니티", "게시 시각(KST)", "조회수",
    "추천수", "댓글수", "인기 점수", "수집 방식", "원문 링크", "Google Docs 링크",
    "요약·썰 포인트", "중복 키", "숏폼 장르", "로그라인", "캐릭터 수",
    "60~120초 대본", "캐릭터 시트", "독립 창작 문서 URL", "제작 상태", "사용됨",
]
SUMMARY_HEADERS = ["날짜", "수집 건수", "중복 제외", "상위 이슈", "비고"]
MAX_ROWS = 2000
MAX_BODY_BYTES = 2 * 1024 * 1024
MAX_CELL_CHARS = 10000
MAX_ITEMS = 12
_COUNT = re.compile(r"(?:0|[1-9][0-9]*)\Z")


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
        text = str(value)
        if len(text) <= MAX_CELL_CHARS:
            return text
    raise ValueError("invalid_cell")


def _date(value: Any) -> date:
    text = _cell(value)
    try:
        parsed = date.fromisoformat(text)
    except ValueError:
        raise ValueError("malformed_date") from None
    if parsed.isoformat() != text:
        raise ValueError("malformed_date")
    return parsed


def _rows(values: Any, expected_headers: list[str]) -> list[list[str]]:
    if not isinstance(values, list) or len(values) >= MAX_ROWS or not values:
        raise ValueError("rows_truncated" if isinstance(values, list) and len(values) >= MAX_ROWS else "invalid_rows")
    if not isinstance(values[0], list):
        raise ValueError("invalid_headers")
    headers = [_cell(item) for item in values[0]]
    if headers != expected_headers:
        raise ValueError("invalid_headers")
    result = []
    for row in values[1:]:
        if not isinstance(row, list) or len(row) > len(expected_headers):
            raise ValueError("invalid_row")
        cells = [_cell(item) for item in row] + [""] * (len(expected_headers) - len(row))
        if any(cells):
            result.append(cells)
    return result


def _canonical_count(value: Any) -> int:
    text = _cell(value)
    if not _COUNT.fullmatch(text):
        raise ValueError("invalid_count")
    return int(text)


def _safe_url(value: str, *, docs: bool = False) -> str:
    if not value:
        return ""
    from urllib.parse import urlsplit

    parsed = urlsplit(value)
    allowed = {"docs.google.com", "drive.google.com"} if docs else None
    if parsed.scheme not in ({"https"} if docs else {"http", "https"}) or not parsed.hostname:
        raise ValueError("invalid_url")
    if docs and parsed.hostname not in allowed:
        raise ValueError("invalid_document_url")
    if parsed.username or parsed.password:
        raise ValueError("invalid_url")
    return value


def evaluate_snapshot(dashboard_rows: Any, summary_rows: Any, now: datetime) -> dict[str, Any]:
    """Validate a bounded Sheets values snapshot using server time in Seoul."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("naive_clock")
    today = now.astimezone(KST).date()
    if now.astimezone(KST).time().replace(tzinfo=None) < time(8, 5):
        return {"status": "WAITING_FOR_UPDATE", "date": today.isoformat(), "reason": "before_cutoff"}

    dashboard = _rows(dashboard_rows, DASHBOARD_HEADERS)
    summaries = _rows(summary_rows, SUMMARY_HEADERS)
    summary_matches = []
    for row in summaries:
        row_date = _date(row[0])
        if row_date == today:
            summary_matches.append(row)
    if len(summary_matches) != 1:
        return {"status": "WAITING_FOR_UPDATE", "date": today.isoformat(), "reason": "summary_missing_or_ambiguous"}
    try:
        count = _canonical_count(summary_matches[0][1])
    except ValueError:
        return {"status": "WAITING_FOR_UPDATE", "date": today.isoformat(), "reason": "summary_incomplete"}

    todays = []
    keys: set[str] = set()
    for row in dashboard:
        row_date = _date(row[0])
        if row_date != today:
            continue
        key = row[14]
        if not key.strip() or len(key) > 200 or key in keys:
            raise ValueError("missing_or_duplicate_source_key")
        keys.add(key)
        if not row[3].strip() or len(row[3]) > 1000:
            raise ValueError("invalid_title")
        if not row[11]:
            raise ValueError("missing_source_url")
        source_url = _safe_url(row[11])
        rank_text = row[1]
        if not _COUNT.fullmatch(rank_text) or int(rank_text) < 1:
            raise ValueError("invalid_rank")
        todays.append((row, source_url))

    if count != len(todays):
        return {"status": "WAITING_FOR_UPDATE", "date": today.isoformat(), "reason": "dashboard_count_mismatch"}
    if count == 0:
        return {"status": "NO_ITEMS", "date": today.isoformat(), "observed_count": 0}

    all_items = []
    for row, source_url in sorted(todays, key=lambda item: (int(item[0][1]), item[0][15])):
        all_items.append({
            "source_key": row[14], "title": row[3], "category": row[2],
            "url": source_url, "original_document_url": _safe_url(row[12], docs=True),
            "source_date": today.isoformat(), "rank": int(row[1]),
        })
    selected_items = all_items[:MAX_ITEMS]
    canonical = {"date": today.isoformat(), "sheet_id": SHEET_ID,
                 "observed_count": count, "items": all_items}
    raw = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {"status": "READY", "date": today.isoformat(), "sheet_id": SHEET_ID,
            "observed_count": count, "items": selected_items, "selected_count": len(selected_items),
            "complete": count <= MAX_ITEMS, "snapshot_sha256": hashlib.sha256(raw).hexdigest()}


def create_sheet_intake_router(
    connection_factory: Callable[[], Any], api_token: str, clock: Callable[[], datetime],
    generation_enabled: Callable[[], bool] = lambda: False,
):
    router = APIRouter()

    @router.post("/v1/intake/sheets")
    async def intake(request: Request, authorization: str | None = Header(default=None)):
        try:
            authorization_bytes = authorization.encode("ascii") if authorization is not None else b""
            token_bytes = api_token.encode("ascii")
        except UnicodeEncodeError:
            raise HTTPException(401, "unauthorized") from None
        if not authorization_bytes.startswith(b"Bearer ") or not hmac.compare_digest(
                authorization_bytes[len(b"Bearer "):], token_bytes):
            raise HTTPException(401, "unauthorized")
        raw_body = bytearray()
        async for chunk in request.stream():
            raw_body.extend(chunk)
            if len(raw_body) > MAX_BODY_BYTES:
                raise HTTPException(413, "request_too_large")
        try:
            body = json.loads(raw_body)
        except Exception:
            raise HTTPException(422, "invalid_body") from None
        if (not isinstance(body, dict) or set(body) != {"dashboard_rows", "summary_rows", "sheet_id"}
                or body.get("sheet_id") != SHEET_ID):
            raise HTTPException(422, "invalid_body")
        try:
            now = clock()
            if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
                raise ValueError("clock_unavailable")
        except Exception:
            raise HTTPException(503, "intake_unavailable") from None
        try:
            result = evaluate_snapshot(body["dashboard_rows"], body["summary_rows"], now)
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
        if result["status"] != "READY":
            return {**result, "auto_generation_enabled": False, "youtube_upload": False}

        payload = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        dedup_key = f"sheet-intake:{SHEET_ID}:{result['date']}"
        try:
            with connection_factory() as db:
                row = db.execute(
                    """INSERT INTO outbox(event_type,aggregate_id,payload,dedup_key)
                       VALUES ('sheet_snapshot_ready',%s,%s,%s)
                       ON CONFLICT(dedup_key) DO NOTHING RETURNING id,payload""",
                    (result["date"], payload, dedup_key),
                ).fetchone()
                if row is None:
                    row = db.execute("SELECT id,payload FROM outbox WHERE dedup_key=%s", (dedup_key,)).fetchone()
                    if row is None:
                        raise HTTPException(503, "intake_unavailable")
                    existing = row["payload"] if isinstance(row["payload"], dict) else json.loads(row["payload"])
                    if existing.get("snapshot_sha256") != result["snapshot_sha256"]:
                        raise HTTPException(409, "snapshot_conflict")
                    state = "ALREADY_ACCEPTED"
                else:
                    state = "ACCEPTED"
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(503, "intake_unavailable") from None
        return {"status": state, "intake_id": str(row["id"]), "date": result["date"],
                "observed_count": result["observed_count"], "selected_count": result["selected_count"],
                "snapshot_sha256": result["snapshot_sha256"], "auto_generation_enabled": bool(generation_enabled()),
                "youtube_upload": False}

    return router
