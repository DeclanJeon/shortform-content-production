"""Authenticated, read-only downloads for validated render artifacts."""
from __future__ import annotations

import hashlib
import hmac
import math
import os
import re
import stat
import uuid
from pathlib import Path
from typing import Callable, Iterator

from fastapi import APIRouter, Header, HTTPException
from starlette.background import BackgroundTask
from fastapi.responses import StreamingResponse


_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_CHUNK_SIZE = 1024 * 1024


def create_artifacts_router(
    connection_factory: Callable[[], object],
    api_token: str,
    artifact_root: str | Path | None = None,
) -> APIRouter:
    """Create the artifact route with the gateway's DB connector and API token."""
    router = APIRouter()
    root = Path(artifact_root or os.environ.get("ARTIFACT_ROOT", "/data/artifacts"))

    @router.get("/v1/jobs/{job_id}/artifact")
    def get_artifact(job_id: str, authorization: str | None = Header(default=None)) -> StreamingResponse:
        if not authorization or not hmac.compare_digest(authorization.removeprefix("Bearer "), api_token):
            raise HTTPException(401, "unauthorized")
        try:
            parsed_job_id = uuid.UUID(job_id)
        except (ValueError, AttributeError):
            raise HTTPException(422, "invalid job id") from None

        try:
            with connection_factory() as db:
                row = db.execute(
                    "SELECT id,stage,status,result FROM generation_jobs WHERE id=%s AND stage='render' AND status='succeeded'",
                    (parsed_job_id,),
                ).fetchone()
        except Exception:
            raise HTTPException(503, "artifact unavailable") from None

        if not isinstance(row, dict):
            raise HTTPException(404, "artifact unavailable")
        result = row.get("result")
        if (str(row.get("id")) != str(parsed_job_id) or row.get("stage") != "render"
                or row.get("status") != "succeeded" or not isinstance(result, dict)):
            raise HTTPException(404, "artifact unavailable")
        try:
            result_job_id = uuid.UUID(result["job_id"])
            artifact_value = result["artifact_path"]
            digest = result["sha256"]
            size = result["size_bytes"]
            codec = result["video_codec"]
            duration = result["duration_seconds"]
        except (KeyError, TypeError, ValueError, AttributeError):
            raise HTTPException(404, "artifact unavailable") from None
        if (result_job_id != parsed_job_id or not isinstance(artifact_value, str)
                or not artifact_value or not isinstance(digest, str) or not _SHA256.fullmatch(digest)
                or isinstance(size, bool) or not isinstance(size, int) or size <= 0
                or codec != "h264" or isinstance(duration, bool)
                or not isinstance(duration, (int, float))
                or (not isinstance(duration, int) and not math.isfinite(duration)) or not 0 < duration <= 180):
            raise HTTPException(404, "artifact unavailable")

        artifact_file = None
        try:
            resolved_root = root.resolve(strict=True)
            candidate = Path(artifact_value)
            if ".." in candidate.parts:
                raise ValueError
            resolved_file = candidate.resolve(strict=True)
            resolved_file.relative_to(resolved_root)
            artifact_file = open(resolved_file, "rb")
            before = os.fstat(artifact_file.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise ValueError

            checksum = hashlib.sha256()
            actual_size = 0
            while chunk := artifact_file.read(_CHUNK_SIZE):
                checksum.update(chunk)
                actual_size += len(chunk)

            after = os.fstat(artifact_file.fileno())
            before_metadata = (before.st_dev, before.st_ino, before.st_mode, before.st_size,
                               before.st_mtime_ns, before.st_ctime_ns)
            after_metadata = (after.st_dev, after.st_ino, after.st_mode, after.st_size,
                              after.st_mtime_ns, after.st_ctime_ns)
            if (before_metadata != after_metadata or actual_size != size or actual_size <= 0
                    or checksum.hexdigest() != digest):
                raise ValueError
            artifact_file.seek(0)
        except (OSError, ValueError, RuntimeError):
            if artifact_file is not None:
                artifact_file.close()
            raise HTTPException(404, "artifact unavailable") from None

        def stream() -> Iterator[bytes]:
            try:
                while chunk := artifact_file.read(_CHUNK_SIZE):
                    yield chunk
            finally:
                artifact_file.close()

        try:
            return StreamingResponse(
                stream(),
                media_type="video/mp4",
                headers={
                    "Content-Disposition": 'attachment; filename="rendered.mp4"',
                    "X-Artifact-SHA256": digest,
                    "Content-Length": str(size),
                    "X-Artifact-Size-Bytes": str(size),
                    "X-Artifact-Codec": codec,
                    "X-Artifact-Duration-Seconds": str(duration),
                },
                background=BackgroundTask(artifact_file.close),
            )
        except BaseException:
            artifact_file.close()
            raise

    return router
