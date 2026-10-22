"""Creator-to-publisher handoff state and gate contracts; no provider calls."""
import copy
import json
import unittest
import uuid
from pathlib import Path
import sys

from fastapi import HTTPException

ROOT=Path(__file__).resolve().parents[1]
sys.dont_write_bytecode=True
sys.path.insert(0,str(ROOT/'src/api'))
from publish_handoff import HandoffAck, _publication_row, create_publish_handoff_router

TOKEN = "creator-handoff-fixture"
SHEET = "publishSheetFixture123"
TAB = "발행 대기"
RENDER = "00000000-0000-4000-8000-000000000021"
FOLDER = "privateFolderFixture123"
DRIVE_FILE = "privateDriveFile123"
SHA256 = "a" * 64
MD5 = "b" * 32


def archive_fixture():
    return {
        "id": 41,
        "aggregate_id": "story-1",
        "status": "ARCHIVED",
        "payload": {
            "story_id": "story-1",
            "task_id": "14",
            "render_job_id": RENDER,
            "production_authorized": True,
            "rights_state": "ORIGINAL_FICTION_VERIFIED",
            "rights_evidence": {
                "rights_evidence_type": "original_creation",
                "rights_evidence_artifact_id": "sha256:" + "c" * 64,
            },
            "qa_checks": {f"QA-0{i}": {"status": "PASS"} for i in range(1, 9)},
            "archive_drive_file_id": DRIVE_FILE,
            "archive_folder_id": FOLDER,
            "archive_file_name": f"render-{RENDER}.mp4",
            "archive_kind": "video",
            "sha256": SHA256,
            "archived_sha256": SHA256,
            "md5": MD5,
            "size_bytes": 1024,
            "archive_google_file": {
                "id": DRIVE_FILE,
                "name": f"render-{RENDER}.mp4",
                "mimeType": "video/mp4",
                "size": 1024,
                "md5Checksum": MD5,
                "parents": [FOLDER],
                "trashed": False,
            },
            "duration_seconds": 48.5,
        },
    }


def creative_fixture():
    return {
        "id": 14,
        "status": "PRIVATE_VIDEO_READY",
        "payload": {
            "story_id": "story-1",
            "run_id": "fixture-run",
            "plan": {
                "title": "검증된 이야기",
                "publishing": {
                    "description": "시트 계약 테스트 설명",
                    "tags": ["이야기", "쇼츠"],
                },
            },
        },
    }


class HandoffDatabase:
    def __init__(self):
        self.archive = archive_fixture()
        self.creative = creative_fixture()
        self.handoff = None
        self.result = None
        self.next_id = 50

    def __enter__(self):
        self.before = (copy.deepcopy(self.archive), copy.deepcopy(self.handoff), self.next_id)
        return self

    def __exit__(self, error_type, error, traceback):
        if error_type is not None:
            self.archive, self.handoff, self.next_id = self.before
        return False

    def execute(self, sql, params=()):
        if sql.startswith("SELECT pg_advisory_xact_lock"):
            self.result = None
        elif "event_type='publish_sheet_handoff' AND status='IN_PROGRESS'" in sql:
            if self.handoff and self.handoff["status"] == "IN_PROGRESS":
                self.result = {**copy.deepcopy(self.handoff), "due": self.handoff.get("due", False)}
            else:
                self.result = None
        elif sql.startswith("SELECT a.id,a.aggregate_id,a.status,a.payload"):
            if self.archive["status"] == "ARCHIVED" and not (
                self.handoff and self.handoff["status"] == "DELIVERED"
            ):
                self.result = copy.deepcopy(self.archive)
            else:
                self.result = None
        elif "event_type='creative_production' FOR UPDATE" in sql:
            self.result = copy.deepcopy(self.creative) if params[0] == self.creative["id"] else None
        elif sql.startswith("INSERT INTO outbox(event_type,aggregate_id,status,payload,dedup_key,next_attempt_at)"):
            self.next_id += 1
            self.handoff = {
                "id": self.next_id,
                "status": "IN_PROGRESS",
                "payload": json.loads(params[1]),
                "due": False,
            }
            self.result = {key: copy.deepcopy(value) for key, value in self.handoff.items() if key != "due"}
        elif sql.startswith("SELECT id,status,payload,next_attempt_at<=now() AS due FROM outbox"):
            self.result = copy.deepcopy(self.handoff) if self.handoff and params[0] == self.handoff["id"] else None
        elif sql.startswith("UPDATE outbox SET status='IN_PROGRESS'"):
            self.handoff["status"] = "IN_PROGRESS"
            self.handoff["payload"] = json.loads(params[0])
            self.handoff["due"] = False
            self.result = None
        elif "event_type='publish_sheet_handoff' FOR UPDATE" in sql:
            self.result = copy.deepcopy(self.handoff) if self.handoff and params[0] == self.handoff["id"] else None
        elif "event_type='automatic_video_archive' FOR UPDATE" in sql:
            self.result = copy.deepcopy(self.archive) if params[0] == self.archive["id"] else None
        elif sql.startswith("UPDATE outbox SET status='DELIVERED'"):
            self.handoff["status"] = "DELIVERED"
            self.handoff["payload"] = json.loads(params[0])
            self.result = None
        else:
            raise AssertionError(f"unexpected SQL: {sql}")
        return self

    def fetchone(self):
        return copy.deepcopy(self.result)


class PublishHandoffTests(unittest.TestCase):
    def setUp(self):
        self.db = HandoffDatabase()
        router = create_publish_handoff_router(
            lambda: self.db, TOKEN, SHEET, TAB, "공개", "22",
        )
        self.routes = {route.path: route.endpoint for route in router.routes}
        self.claim_endpoint = self.routes["/v1/publish-handoffs/claim"]
        self.ack_endpoint = self.routes["/v1/publish-handoffs/{handoff_id}/ack"]
        self.auth = f"Bearer {TOKEN}"

    def claim(self):
        return self.claim_endpoint(authorization=self.auth)

    def test_verified_archive_becomes_one_public_queue_row_without_upload(self):
        result = self.claim()
        values = result["row_values"]
        self.assertEqual(result["state"], "HANDOFF_IN_PROGRESS")
        self.assertEqual(len(values), 15)
        self.assertTrue(values[0].startswith("sp-"))
        self.assertEqual(values[1], f"https://drive.google.com/file/d/{DRIVE_FILE}/view")
        self.assertEqual(values[6:9], ["공개", "대기", ""])
        self.assertEqual(values[12:15], ["0", "", ""])
        self.assertNotIn("video_id", result)
        self.assertEqual(self.claim()["state"], "NO_HANDOFF_READY")

    def test_ack_requires_the_claim_and_exact_sheet_readback(self):
        result = self.claim()
        body = HandoffAck(
            claim_id=uuid.UUID(result["claim_id"]),
            publish_id=result["publish_id"],
            sheet_id=SHEET,
            queue_tab=TAB,
            row_number=2,
            row_values=result["row_values"],
        )
        with self.assertRaises(HTTPException) as denied:
            self.ack_endpoint(handoff_id=result["handoff_id"], body=body, authorization=None)
        self.assertEqual(denied.exception.status_code, 401)
        self.assertEqual(
            self.ack_endpoint(handoff_id=result["handoff_id"], body=body, authorization=self.auth)["state"],
            "DELIVERED",
        )
        self.assertEqual(
            self.ack_endpoint(handoff_id=result["handoff_id"], body=body, authorization=self.auth)["state"],
            "DELIVERED",
        )
        self.assertEqual(self.claim()["state"], "NO_HANDOFF_READY")

    def test_queue_metadata_mismatch_and_stale_claim_fail_closed(self):
        result = self.claim()
        values = list(result["row_values"])
        values[1] = "https://drive.google.com/file/d/otherDriveFile456/view"
        body = HandoffAck(
            claim_id=uuid.UUID(result["claim_id"]),
            publish_id=result["publish_id"],
            sheet_id=SHEET,
            queue_tab=TAB,
            row_number=2,
            row_values=values,
        )
        with self.assertRaises(HTTPException) as mismatch:
            self.ack_endpoint(handoff_id=result["handoff_id"], body=body, authorization=self.auth)
        self.assertEqual(mismatch.exception.status_code, 409)
        stale = body.model_copy(update={"claim_id": uuid.uuid4(), "row_values": result["row_values"]})
        with self.assertRaises(HTTPException) as conflict:
            self.ack_endpoint(handoff_id=result["handoff_id"], body=stale, authorization=self.auth)
        self.assertEqual(conflict.exception.status_code, 409)
        self.assertEqual(self.db.handoff["status"], "IN_PROGRESS")

    def test_rights_qa_and_archive_readback_are_required(self):
        for defect in ("rights", "qa", "archive"):
            with self.subTest(defect=defect):
                self.db = HandoffDatabase()
                self.routes = {route.path: route.endpoint for route in create_publish_handoff_router(
                    lambda: self.db, TOKEN, SHEET, TAB, "공개", "22",
                ).routes}
                self.claim_endpoint = self.routes["/v1/publish-handoffs/claim"]
                if defect == "rights":
                    self.db.archive["payload"]["rights_state"] = "UNVERIFIED"
                elif defect == "qa":
                    self.db.archive["payload"]["qa_checks"]["QA-08"]["status"] = "REVISE"
                else:
                    self.db.archive["payload"]["archive_google_file"]["md5Checksum"] = "c" * 32
                with self.assertRaises(HTTPException) as held:
                    self.claim()
                self.assertEqual(held.exception.status_code, 409)
                self.assertIsNone(self.db.handoff)

    def test_an_unarchived_video_is_not_handed_off(self):
        self.db.archive["status"] = "ARCHIVE_IN_PROGRESS"
        self.assertEqual(self.claim()["state"], "NO_HANDOFF_READY")
        self.assertIsNone(self.db.handoff)


if __name__ == "__main__":
    unittest.main()
