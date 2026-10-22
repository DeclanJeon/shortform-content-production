"""Authenticated ledger for idempotent private Google Drive archives."""
from __future__ import annotations

import hmac
import json
import re
import uuid
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from typing import Protocol

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from archive_receipt import verified_archive

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_MD5 = re.compile(r"[0-9a-f]{32}\Z")
_DRIVE_ID = re.compile(r"[A-Za-z0-9_-]{10,200}\Z")
_SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,119}\Z")


class Cursor(Protocol):
    def fetchone(self) -> Mapping[str, object] | None: ...


class Database(Protocol):
    def execute(self, sql: str, params: tuple[object, ...] = ()) -> Cursor: ...


class BindDriveFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    claim_id: uuid.UUID
    drive_file_id: str = Field(pattern=r"^[A-Za-z0-9_-]{10,200}$")


class GoogleFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{10,200}$")
    name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")
    mimeType: str
    size: int = Field(gt=0, strict=True)
    md5Checksum: str = Field(pattern=r"^[0-9a-f]{32}$")
    parents: list[str] = Field(min_length=1, max_length=1)
    trashed: bool


class ArchiveAck(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    claim_id: uuid.UUID
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    google_file: GoogleFile


def create_automatic_archive_router(
    connection_factory: Callable[[], AbstractContextManager[Database]],
    api_token: str,
    drive_folder_id: str,
    kind: str = 'video',
) -> APIRouter:
    """Create claim, stable-ID binding, and verified Drive acknowledgement routes."""
    if drive_folder_id and not _DRIVE_ID.fullmatch(drive_folder_id):
        raise ValueError("invalid automatic archive folder id")
    if kind not in ('video','project'):raise ValueError('invalid archive kind')
    event_type='automatic_video_archive' if kind=='video' else 'automatic_project_archive'
    prefix='/v1/automatic-archives' if kind=='video' else '/v1/project-archives'
    mime='video/mp4' if kind=='video' else 'application/zip'
    router = APIRouter()

    def authorize(value: str | None) -> None:
        supplied = value[7:] if isinstance(value, str) and value.startswith("Bearer ") else ""
        try:
            valid = bool(supplied) and hmac.compare_digest(supplied.encode("ascii"), api_token.encode("ascii"))
        except UnicodeEncodeError:
            valid = False
        if not valid:
            raise HTTPException(401, "unauthorized")
        if not drive_folder_id:
            raise HTTPException(503, "automatic_archive_unconfigured")

    def payload(row: Mapping[str, object]) -> dict[str, object]:
        raw = row.get("payload")
        if isinstance(raw, str):
            parsed = json.loads(raw)
        elif isinstance(raw, dict):
            parsed = raw
        else:
            raise HTTPException(409, "archive_payload_invalid")
        return parsed

    def load(db: Database, archive_id: int) -> tuple[Mapping[str, object], dict[str, object]]:
        row = db.execute(
            "SELECT id,status,payload FROM outbox "
            f"WHERE id=%s AND event_type='{event_type}' FOR UPDATE",
            (archive_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(404, "archive_not_found")
        return row, payload(row)

    def envelope(row: Mapping[str, object], data: dict[str, object]) -> dict[str, object]:
        required = ("story_id", "render_job_id", "sha256", "md5", "size_bytes")
        if any(key not in data for key in required):
            raise HTTPException(409, "archive_payload_invalid")
        try:
            render_id = uuid.UUID(str(data["render_job_id"]))
            size = int(data["size_bytes"])
        except (ValueError, TypeError):
            raise HTTPException(409, "archive_payload_invalid") from None
        sha256 = str(data["sha256"])
        md5 = str(data["md5"])
        file_name = str(data.get('archive_file_name') or (f'render-{render_id}.mp4' if kind=='video' else f'project-{render_id}.zip'))
        if (
            data.get("publication_authorized") is not False
            or not _SHA256.fullmatch(sha256)
            or not _MD5.fullmatch(md5)
            or size <= 0
            or data.get("archive_folder_id") != drive_folder_id
            or not _SAFE_NAME.fullmatch(file_name)
        ):
            raise HTTPException(409, "archive_payload_invalid")
        return {
            "state": row["status"],
            "archive_id": int(row["id"]),
            "claim_id": data["archive_claim_id"],
            "drive_file_id": data.get("archive_drive_file_id"),
            "folder_id": drive_folder_id,
            "file_name": file_name,
            "sha256": sha256,
            "md5": md5,
            "size_bytes": size,
            "render_job_id": str(render_id),
            'mime_type':mime,'codec':'h264' if kind=='video' else 'zip',
            "artifact_url": f"/v1/jobs/{render_id}/artifact" if kind=='video' else f'/v1/project-bundles/{render_id}/artifact?archive_id={int(row["id"])}',
            "publication": False,
            "youtube_upload": False,
        }

    @router.post(prefix+'/claim')
    def claim(authorization: str | None = Header(default=None)) -> dict[str, object]:
        authorize(authorization)
        try:
            with connection_factory() as db:
                row = db.execute(
                    "SELECT id,status,payload FROM outbox "
                    f"WHERE event_type='{event_type}' "
                    "AND status IN ('ARCHIVE_IN_PROGRESS','PENDING') "
                    "AND next_attempt_at<=now() "
                    "ORDER BY CASE status WHEN 'ARCHIVE_IN_PROGRESS' THEN 0 ELSE 1 END,id "
                    "LIMIT 1 FOR UPDATE SKIP LOCKED"
                ).fetchone()
                if row is None:
                    return {"state": "NO_ARCHIVE_READY", "publication": False, "youtube_upload": False}
                data = payload(row)
                if data.get("archive_folder_id", drive_folder_id) != drive_folder_id:
                    raise HTTPException(409, "archive_destination_changed")
                # A new lease fences stale acknowledgements, while the Drive ID
                # survives every retry and prevents duplicate file allocation.
                data["archive_claim_id"] = str(uuid.uuid4())
                data["archive_folder_id"] = drive_folder_id
                data['archive_kind']=kind
                data['archive_file_name']=(f"render-{data.get('render_job_id','video')}.mp4" if kind=='video' else f"project-{data['render_job_id']}.zip")
                db.execute(
                    "UPDATE outbox SET status='ARCHIVE_IN_PROGRESS',attempts=attempts+1,"
                    "next_attempt_at=now()+interval '15 minutes',payload=%s WHERE id=%s",
                    (json.dumps(data, ensure_ascii=False), row["id"]),
                )
                row = {**row, "status": "ARCHIVE_IN_PROGRESS", "payload": data}
                return envelope(row, data)
        except HTTPException:
            raise
        except (OSError, RuntimeError, TypeError, ValueError, json.JSONDecodeError):
            raise HTTPException(503, "archive_claim_unavailable") from None

    @router.post(prefix+'/{archive_id}/bind')
    def bind(
        archive_id: int,
        body: BindDriveFile,
        authorization: str | None = Header(default=None),
    ) -> dict[str, object]:
        authorize(authorization)
        try:
            with connection_factory() as db:
                row, data = load(db, archive_id)
                if row["status"] != "ARCHIVE_IN_PROGRESS" or data.get("archive_claim_id") != str(body.claim_id):
                    raise HTTPException(409, "archive_claim_conflict")
                canonical = data.get("archive_drive_file_id")
                if canonical is None:
                    data["archive_drive_file_id"] = body.drive_file_id
                    db.execute("UPDATE outbox SET payload=%s WHERE id=%s", (json.dumps(data), archive_id))
                elif canonical != body.drive_file_id and not _DRIVE_ID.fullmatch(str(canonical)):
                    raise HTTPException(409, "archive_drive_identity_invalid")
                return envelope(row, data)
        except HTTPException:
            raise
        except (OSError, RuntimeError, TypeError, ValueError, json.JSONDecodeError):
            raise HTTPException(503, "archive_bind_unavailable") from None

    @router.post(prefix+'/{archive_id}/ack')
    def acknowledge(
        archive_id: int,
        body: ArchiveAck,
        authorization: str | None = Header(default=None),
    ) -> dict[str, object]:
        authorize(authorization)
        try:
            with connection_factory() as db:
                row, data = load(db, archive_id)
                if row["status"] == "ARCHIVED":
                    try:
                        verified_archive(row, data, drive_folder_id,kind)
                    except ValueError:
                        raise HTTPException(409, "archive_ack_conflict") from None
                    if (
                        data.get("archive_claim_id") != str(body.claim_id)
                        or body.sha256 != data.get("sha256")
                        or data.get("archive_google_file") != body.google_file.model_dump()
                    ):
                        raise HTTPException(409, "archive_ack_conflict")
                    return {"state": "ARCHIVED", "archive_id": archive_id, "publication": False, "youtube_upload": False}
                google = body.google_file
                if (
                    row["status"] != "ARCHIVE_IN_PROGRESS"
                    or data.get("archive_claim_id") != str(body.claim_id)
                    or data.get("archive_drive_file_id") != google.id
                    or data.get("archive_folder_id") != drive_folder_id
                    or body.sha256 != data.get("sha256")
                    or google.name != data.get("archive_file_name")
                    or google.mimeType != mime
                    or google.size != data.get("size_bytes")
                    or google.md5Checksum != data.get("md5")
                    or google.parents != [drive_folder_id]
                    or google.trashed
                ):
                    raise HTTPException(409, "archive_google_readback_mismatch")
                data["archive_google_file"] = google.model_dump()
                data["archived_sha256"] = body.sha256
                db.execute(
                    "UPDATE outbox SET status='ARCHIVED',payload=%s WHERE id=%s",
                    (json.dumps(data, ensure_ascii=False), archive_id),
                )
                return {
                    "state": "ARCHIVED",
                    "archive_id": archive_id,
                    "drive_file_id": google.id,
                    "sha256": body.sha256,
                    "publication": False,
                    "youtube_upload": False,
                }
        except HTTPException:
            raise
        except (OSError, RuntimeError, TypeError, ValueError, json.JSONDecodeError):
            raise HTTPException(503, "archive_ack_unavailable") from None

    return router
