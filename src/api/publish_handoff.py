"""Deliver verified private render archives to the separate publisher queue."""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import uuid
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from typing import Protocol, TypeAlias

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from archive_receipt import verified_archive

JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
DbValue: TypeAlias = JsonValue | uuid.UUID
DbRow: TypeAlias = Mapping[str, DbValue]
PUBLISH_HEADERS = (
    "발행ID", "영상링크", "제목", "설명", "태그", "카테고리", "공개설정", "상태",
    "유튜브ID", "발행URL", "발행시각", "오류", "시도횟수", "발행키", "클레임시각",
)
PRIVACY_LABELS = {"공개": "public", "비공개": "private", "비공개목록": "unlisted"}
READY_PRIVACY = frozenset(PRIVACY_LABELS)
QUEUE_STATES = frozenset(("대기", "진행중", "완료", "실패"))
RIGHTS = {
    "PERMISSION_VERIFIED": frozenset(("license", "written_permission", "public_domain")),
    "ORIGINAL_FICTION_VERIFIED": frozenset(("original_creation",)),
}


class Cursor(Protocol):
    def fetchone(self) -> DbRow | None: ...


class Database(Protocol):
    def execute(self, sql: str, params: tuple[DbValue, ...] = ()) -> Cursor: ...


class HandoffAck(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    claim_id: uuid.UUID
    publish_id: str = Field(pattern=r"^sp-[0-9a-f]{32}$")
    sheet_id: str = Field(min_length=10, max_length=120)
    queue_tab: str = Field(min_length=1, max_length=100)
    row_number: int = Field(ge=2, le=20000)
    row_values: list[str] = Field(min_length=15, max_length=15)


def _authorized(value: str | None, token: str) -> None:
    supplied = value[7:] if isinstance(value, str) and value.startswith("Bearer ") else ""
    try:
        valid = bool(supplied) and hmac.compare_digest(supplied.encode("ascii"), token.encode("ascii"))
    except UnicodeEncodeError:
        valid = False
    if not valid:
        raise HTTPException(401, "unauthorized")


def _payload(row: DbRow) -> dict[str, JsonValue]:
    value = row.get("payload")
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            raise HTTPException(409, "publish_handoff_payload_invalid") from None
    else:
        parsed = value
    if not isinstance(parsed, dict):
        raise HTTPException(409, "publish_handoff_payload_invalid")
    return parsed


def _publication_row(archive: DbRow, creative: DbRow, privacy: str, category: str) -> dict[str, JsonValue]:
    media = _payload(archive)
    creative_data = _payload(creative)
    if archive.get("status") != "ARCHIVED" or media.get("production_authorized") is not True:
        raise HTTPException(409, "verified_production_archive_required")
    rights = media.get("rights_evidence")
    rights_state = media.get("rights_state")
    if (not isinstance(rights, dict) or not isinstance(rights_state, str) or rights_state not in RIGHTS
            or rights.get("rights_evidence_type") not in RIGHTS.get(str(rights_state), frozenset())
            or not isinstance(rights.get("rights_evidence_artifact_id"), str)
            or not rights["rights_evidence_artifact_id"]):
        raise HTTPException(409, "verified_rights_evidence_required")
    checks = media.get("qa_checks")
    if not isinstance(checks, dict) or set(checks) != {f"QA-0{i}" for i in range(1, 9)}:
        raise HTTPException(409, "complete_production_qa_required")
    if any(not isinstance(check, dict) or check.get("status") != "PASS" for check in checks.values()):
        raise HTTPException(409, "production_qa_not_passed")
    plan = creative_data.get("plan")
    if not isinstance(plan, dict):
        raise HTTPException(409, "approved_plan_missing")
    title = plan.get("title")
    publishing = plan.get("publishing")
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 100 or not isinstance(publishing, dict):
        raise HTTPException(409, "publish_metadata_invalid")
    description = publishing.get("description", "")
    tags = publishing.get("tags", [])
    if not isinstance(description, str) or len(description) > 5000:
        raise HTTPException(409, "publish_metadata_invalid")
    if not isinstance(tags, list) or any(not isinstance(tag, str) or "," in tag for tag in tags):
        raise HTTPException(409, "publish_metadata_invalid")
    try:
        archive_identity = verified_archive(archive, media, kind="video")
    except ValueError:
        raise HTTPException(409, "verified_drive_archive_required") from None
    story_id = media.get("story_id")
    render_id = archive_identity["render_job_id"]
    if (not isinstance(story_id, str) or not story_id
            or str(archive.get("aggregate_id")) != story_id
            or creative_data.get("story_id") != story_id):
        raise HTTPException(409, "publish_handoff_identity_invalid")
    digest = hashlib.sha256(f"{story_id}:{render_id}".encode("utf-8")).hexdigest()[:32]
    publish_id = "sp-" + digest
    drive_id = archive_identity["drive_file_id"]
    values = [
        publish_id,
        f"https://drive.google.com/file/d/{drive_id}/view",
        title.strip(),
        description,
        ",".join(tags),
        category,
        privacy,
        "대기",
        "",
        "",
        "",
        "",
        "0",
        "",
        "",
    ]
    return {
        "archive_id": str(archive["id"]),
        "story_id": story_id,
        "render_job_id": render_id,
        "publish_id": publish_id,
        "sheet_id": "",
        "queue_tab": "",
        "row_values": values,
        "archive_sha256": str(archive_identity["sha256"]),
    }


def create_publish_handoff_router(
    connection_factory: Callable[[], AbstractContextManager[Database]],
    api_token: str,
    sheet_id: str,
    queue_tab: str,
    privacy: str,
    category_id: str,
) -> APIRouter:
    router = APIRouter(prefix="/v1/publish-handoffs")
    configured = bool(
        re.fullmatch(r"[A-Za-z0-9_-]{10,120}", sheet_id)
        and queue_tab
        and privacy in READY_PRIVACY
        and re.fullmatch(r"[0-9]{1,4}", category_id)
    )

    def authorize(value: str | None) -> None:
        _authorized(value, api_token)
        if not configured:
            raise HTTPException(503, "publish_handoff_unconfigured")

    def claim_event(db: Database, row: DbRow, payload: dict[str, JsonValue]) -> DbRow:
        claim_id = str(uuid.uuid4())
        payload.update(claim_id=claim_id, sheet_id=sheet_id, queue_tab=queue_tab)
        dedup_key = "publish-sheet-handoff:" + str(payload["render_job_id"])
        inserted = db.execute(
            "INSERT INTO outbox(event_type,aggregate_id,status,payload,dedup_key,next_attempt_at) "
            "VALUES('publish_sheet_handoff',%s,'IN_PROGRESS',%s,%s,now()+interval '15 minutes') "
            "ON CONFLICT(dedup_key) DO NOTHING RETURNING id,status,payload",
            (str(payload["story_id"]), json.dumps(payload, ensure_ascii=False), dedup_key),
        ).fetchone()
        if inserted is not None:
            return inserted
        existing = db.execute(
            "SELECT id,status,payload,next_attempt_at<=now() AS due FROM outbox "
            "WHERE dedup_key=%s AND event_type='publish_sheet_handoff' FOR UPDATE",
            (dedup_key,),
        ).fetchone()
        if existing is None:
            raise HTTPException(503, "publish_handoff_claim_unavailable")
        if existing.get("status") == "DELIVERED":
            return existing
        if existing.get("status") == "IN_PROGRESS" and existing.get("due") is not True:
            return existing
        db.execute(
            "UPDATE outbox SET status='IN_PROGRESS',payload=%s,attempts=attempts+1,"
            "next_attempt_at=now()+interval '15 minutes' WHERE id=%s",
            (json.dumps(payload, ensure_ascii=False), existing["id"]),
        )
        return {"id": existing["id"], "status": "IN_PROGRESS", "payload": payload}

    @router.post("/claim")
    def claim(authorization: str | None = Header(default=None)) -> dict[str, JsonValue]:
        authorize(authorization)
        with connection_factory() as db:
            db.execute("SELECT pg_advisory_xact_lock(hashtext('shortform-publish-sheet-handoff'))")
            active = db.execute(
                "SELECT id,status,payload,next_attempt_at<=now() AS due FROM outbox "
                "WHERE event_type='publish_sheet_handoff' AND status='IN_PROGRESS' "
                "ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1"
            ).fetchone()
            if active is not None:
                if active.get("due") is not True:
                    return {"state": "NO_HANDOFF_READY", "reason": "handoff_claim_in_progress"}
                payload = _payload(active)
                existing_values = payload.get("row_values")
                if (payload.get("sheet_id") != sheet_id or payload.get("queue_tab") != queue_tab
                        or not isinstance(existing_values, list) or len(existing_values) != 15
                        or existing_values[5] != category_id or existing_values[6] != privacy):
                    raise HTTPException(409, "publish_handoff_configuration_changed")
                claim_id = str(uuid.uuid4())
                payload.update(claim_id=claim_id, sheet_id=sheet_id, queue_tab=queue_tab)
                db.execute(
                    "UPDATE outbox SET payload=%s,attempts=attempts+1,next_attempt_at=now()+interval '15 minutes' "
                    "WHERE id=%s",
                    (json.dumps(payload, ensure_ascii=False), active["id"]),
                )
                return {"state": "HANDOFF_IN_PROGRESS", "handoff_id": int(active["id"]), **payload}
            archive = db.execute(
                "SELECT a.id,a.aggregate_id,a.status,a.payload FROM outbox a "
                "WHERE a.event_type='automatic_video_archive' AND a.status='ARCHIVED' "
                "AND a.payload->>'production_authorized'='true' "
                "AND ((a.payload->>'rights_state'='PERMISSION_VERIFIED' "
                "AND a.payload->'rights_evidence'->>'rights_evidence_type' IN ('license','written_permission','public_domain')) "
                "OR (a.payload->>'rights_state'='ORIGINAL_FICTION_VERIFIED' "
                "AND a.payload->'rights_evidence'->>'rights_evidence_type'='original_creation')) "
                "AND a.payload->'rights_evidence'->>'rights_evidence_artifact_id' IS NOT NULL "
                "AND jsonb_typeof(a.payload->'qa_checks')='object' "
                "AND (SELECT count(*) FROM jsonb_each(CASE WHEN jsonb_typeof(a.payload->'qa_checks')='object' "
                "THEN a.payload->'qa_checks' ELSE '{}'::jsonb END))=8 "
                "AND NOT EXISTS (SELECT 1 FROM jsonb_each(CASE WHEN jsonb_typeof(a.payload->'qa_checks')='object' "
                "THEN a.payload->'qa_checks' ELSE '{}'::jsonb END) q "
                "WHERE q.value->>'status' IS DISTINCT FROM 'PASS') "
                "AND (COALESCE(a.payload->>'project_archive_required','false')='false' OR EXISTS ("
                "SELECT 1 FROM outbox p WHERE p.event_type='automatic_project_archive' "
                "AND p.aggregate_id=a.aggregate_id AND p.status='ARCHIVED' "
                "AND p.payload->>'render_job_id'=a.payload->>'render_job_id')) "
                "AND NOT EXISTS (SELECT 1 FROM outbox h WHERE h.event_type='publish_sheet_handoff' "
                "AND h.payload->>'render_job_id'=a.payload->>'render_job_id' AND h.status='DELIVERED') "
                "AND NOT EXISTS (SELECT 1 FROM outbox r WHERE r.event_type='manual_youtube_release' "
                "AND r.payload->'manifest'->>'render_job_id'=a.payload->>'render_job_id' "
                "AND r.status IN ('PREPARED','PRIVATE_UPLOADED','PUBLISHED')) "
                "ORDER BY a.id FOR UPDATE SKIP LOCKED LIMIT 1"
            ).fetchone()
            if archive is None:
                return {"state": "NO_HANDOFF_READY", "reason": "no_verified_archive"}
            archive_payload = _payload(archive)
            try:
                task_id = int(str(archive_payload["task_id"]))
            except (KeyError, ValueError, TypeError):
                raise HTTPException(409, "publish_handoff_lineage_invalid") from None
            creative = db.execute(
                "SELECT id,status,payload FROM outbox WHERE id=%s AND event_type='creative_production' FOR UPDATE",
                (task_id,),
            ).fetchone()
            if creative is None:
                raise HTTPException(409, "publish_handoff_lineage_invalid")
            values = _publication_row(archive, creative, privacy, category_id)
            values.update(sheet_id=sheet_id, queue_tab=queue_tab)
            event = claim_event(db, archive, values)
            if event.get("status") == "DELIVERED":
                return {"state": "NO_HANDOFF_READY", "reason": "handoff_already_delivered"}
            return {"state": "HANDOFF_IN_PROGRESS", "handoff_id": int(event["id"]), **values}

    @router.post("/{handoff_id}/ack")
    def acknowledge(
        handoff_id: int,
        body: HandoffAck,
        authorization: str | None = Header(default=None),
    ) -> dict[str, JsonValue]:
        authorize(authorization)
        with connection_factory() as db:
            row = db.execute(
                "SELECT id,status,payload FROM outbox "
                "WHERE id=%s AND event_type='publish_sheet_handoff' FOR UPDATE",
                (handoff_id,),
            ).fetchone()
            if row is None:
                raise HTTPException(404, "publish_handoff_not_found")
            payload = _payload(row)
            if (payload.get("publish_id") != body.publish_id or payload.get("sheet_id") != body.sheet_id
                    or payload.get("queue_tab") != body.queue_tab
                    or str(payload.get("claim_id")) != str(body.claim_id)):
                raise HTTPException(409, "publish_handoff_claim_conflict")
            if body.sheet_id != sheet_id or body.queue_tab != queue_tab:
                raise HTTPException(409, "publish_handoff_configuration_changed")
            try:
                archive_id = int(str(payload["archive_id"]))
            except (KeyError, ValueError, TypeError):
                raise HTTPException(409, "publish_handoff_archive_identity_invalid") from None
            archive = db.execute(
                "SELECT id,aggregate_id,status,payload FROM outbox "
                "WHERE id=%s AND event_type='automatic_video_archive' FOR UPDATE",
                (archive_id,),
            ).fetchone()
            if archive is None or archive.get("status") != "ARCHIVED":
                raise HTTPException(409, "publish_handoff_archive_changed")
            try:
                archive_identity = verified_archive(archive, _payload(archive), kind="video")
            except (HTTPException, ValueError):
                raise HTTPException(409, "publish_handoff_archive_changed") from None
            if (archive_identity["render_job_id"] != payload.get("render_job_id")
                    or archive_identity["sha256"] != payload.get("archive_sha256")
                    or str(archive.get("aggregate_id")) != payload.get("story_id")):
                raise HTTPException(409, "publish_handoff_archive_changed")
            if body.row_values[0] != body.publish_id or body.row_values[0:7] != payload.get("row_values", [])[:7]:
                raise HTTPException(409, "publish_handoff_readback_mismatch")
            if body.row_values[7] not in QUEUE_STATES:
                raise HTTPException(409, "publish_handoff_queue_state_invalid")
            readback_sha256 = hashlib.sha256(
                json.dumps(body.row_values, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            if row.get("status") == "DELIVERED":
                prior = payload.get("handoff_readback")
                if isinstance(prior, dict) and prior.get("readback_sha256") == readback_sha256:
                    return {"state": "DELIVERED", "handoff_id": handoff_id, "publish_id": body.publish_id}
                raise HTTPException(409, "publish_handoff_ack_conflict")
            if row.get("status") != "IN_PROGRESS":
                raise HTTPException(409, "publish_handoff_claim_conflict")
            payload["handoff_readback"] = {
                "row_number": body.row_number,
                "row_values": body.row_values,
                "readback_sha256": readback_sha256,
                "verified": True,
            }
            db.execute(
                "UPDATE outbox SET status='DELIVERED',payload=%s,next_attempt_at=now()+interval '6 hours' "
                "WHERE id=%s",
                (json.dumps(payload, ensure_ascii=False), handoff_id),
            )
        return {"state": "DELIVERED", "handoff_id": handoff_id, "publish_id": body.publish_id}

    return router
