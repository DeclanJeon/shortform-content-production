"""Pure validation of the durable Drive readback required before publication."""
import re
import uuid


_ID = re.compile(r"[A-Za-z0-9_-]{10,200}\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_MD5 = re.compile(r"[0-9a-f]{32}\Z")


def verified_archive(row, payload, expected_folder="", kind="video"):
    """Return immutable lineage; never treat queued media as an archived asset."""
    if kind not in ('video','project'):raise ValueError('archive_kind_invalid')
    mime='video/mp4' if kind=='video' else 'application/zip'
    if row.get("status") != "ARCHIVED":
        raise ValueError("verified_archive_required")
    google = payload.get("archive_google_file")
    folder = payload.get("archive_folder_id")
    sha = payload.get("sha256")
    md5 = payload.get("md5")
    size = payload.get("size_bytes")
    file_id = payload.get("archive_drive_file_id")
    try:
        render = str(uuid.UUID(payload["render_job_id"]))
    except (KeyError, ValueError, TypeError, AttributeError):
        raise ValueError("archive_receipt_invalid") from None
    if (
        not isinstance(google, dict)
        or not isinstance(folder, str) or not _ID.fullmatch(folder)
        or (expected_folder and folder != expected_folder)
        or not isinstance(file_id, str) or not _ID.fullmatch(file_id)
        or not isinstance(sha, str) or not _SHA.fullmatch(sha)
        or not isinstance(md5, str) or not _MD5.fullmatch(md5)
        or type(size) is not int or size <= 0
        or payload.get("archived_sha256") != sha
        or payload.get("archive_kind",'video')!=kind
        or payload.get("archive_file_name") != (f"render-{render}.mp4" if kind=='video' else f"project-{render}.zip")
    ):
        raise ValueError("archive_receipt_invalid")
    expected = {"id": file_id, "name": payload["archive_file_name"],
                "mimeType": mime, "size": size, "md5Checksum": md5,
                "parents": [folder], "trashed": False}
    if google != expected or type(google.get("size")) is not int or google.get("trashed") is not False:
        raise ValueError("archive_receipt_invalid")
    return {"archive_id": str(row["id"]), "artifact_kind":kind,"drive_file_id": file_id,
            "folder_id": folder, "sha256": sha, "md5": md5, "size_bytes": size,
            "render_job_id": render}
