#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///

# --- How to run ---
# 1. Install Docker and ensure the pinned n8n image is available.
# 2. Run: python3 tests/run_creator_handoff_e2e.py
# 3. This test uses isolated SQLite and local HTTP mocks; it never contacts Google or YouTube.
# -----------------

from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
from threading import Lock, Thread
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src/distribution"))
from workflows import render

IMAGE = "docker.n8n.io/n8nio/n8n:2.43.5"
WORKFLOW_ID = "SFAutomaticPublishQueueHandoffE2E"
SHEET_ID = "publishSheetFixture123"
TAB = "발행 대기"
PUBLISH_ID = "sp-" + "a" * 32
VIDEO_ID = "fixture-video-id-not-youtube"
PUBLISH_HEADERS = [
    "발행ID", "영상링크", "제목", "설명", "태그", "카테고리", "공개설정", "상태",
    "유튜브ID", "발행URL", "발행시각", "오류", "시도횟수", "발행키", "클레임시각",
]
EXPECTED_ROW = [
    PUBLISH_ID,
    "https://drive.google.com/file/d/privateDriveFile123/view",
    "테스트 제목",
    "시트 handoff 테스트 설명",
    "이야기,쇼츠",
    "22",
    "공개",
    "대기",
    "",
    "",
    "",
    "",
    "0",
    "",
    "",
]
REQUESTS: list[str] = []
QUEUE_ROWS: list[list[str]] = []
LOCK = Lock()
CLAIM_ID = "00000000-0000-4000-8000-000000000099"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args) -> None:
        return

    def send_json(self, value: dict, status: int = 200) -> None:
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_body(self) -> dict:
        size = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(size) if size else b"{}"
        return json.loads(raw.decode("utf-8"))

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if "/values/" not in path:
            self.send_json({"error": "unexpected_get", "path": path}, 404)
            return
        with LOCK:
            REQUESTS.append("sheets_read")
            rows = [PUBLISH_HEADERS, *QUEUE_ROWS]
        self.send_json({"majorDimension": "ROWS", "values": rows})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        body = self.read_body()
        if path == "/v1/publish-handoffs/claim":
            with LOCK:
                REQUESTS.append("claim")
            self.send_json({
                "state": "HANDOFF_IN_PROGRESS",
                "handoff_id": 101,
                "claim_id": CLAIM_ID,
                "publish_id": PUBLISH_ID,
                "sheet_id": SHEET_ID,
                "queue_tab": TAB,
                "row_values": EXPECTED_ROW,
            })
            return
        if "/values/" in path and path.endswith(":append"):
            values = body.get("values")
            if not isinstance(values, list) or values != [EXPECTED_ROW]:
                self.send_json({"error": "append_payload_mismatch"}, 409)
                return
            with LOCK:
                REQUESTS.append("sheets_append")
                QUEUE_ROWS.extend(values)
            self.send_json({"updates": {"updatedRows": 1, "updatedRange": "'발행 대기'!A2:O2"}})
            return
        if path == "/v1/publish-handoffs/101/ack":
            with LOCK:
                REQUESTS.append("ack")
                row_number = len(QUEUE_ROWS) + 1
                acknowledged = dict(body)
            if (acknowledged.get("claim_id") != CLAIM_ID
                    or acknowledged.get("publish_id") != PUBLISH_ID
                    or acknowledged.get("row_values") != EXPECTED_ROW
                    or row_number != 2):
                self.send_json({"error": "ack_payload_mismatch"}, 409)
                return
            self.send_json({"state": "DELIVERED", "handoff_id": 101, "publish_id": PUBLISH_ID})
            return
        self.send_json({"error": "unexpected_post", "path": path}, 404)


def subprocess_result(argv: list[str], *, timeout: int = 900) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)


def execute_n8n(command: list[str], data_dir: Path, fixtures: Path) -> subprocess.CompletedProcess[str]:
    return subprocess_result([
        "docker", "run", "--rm", "--network", "host",
        "--env", "N8N_ENCRYPTION_KEY=creator-handoff-e2e-encryption-key-012345",
        "--env", "N8N_DIAGNOSTICS_ENABLED=false",
        "--env", "N8N_PERSONALIZATION_ENABLED=false",
        "--env", "N8N_SECURE_COOKIE=false",
        "--env", "N8N_BLOCK_ENV_ACCESS_IN_NODE=true",
        "--env", "N8N_RUNNERS_ENABLED=true",
        "--env", "GENERIC_TIMEZONE=Asia/Seoul",
        "--volume", str(data_dir) + ":/home/node/.n8n",
        "--volume", str(fixtures) + ":/fixtures:ro",
        "--entrypoint", "n8n",
        IMAGE,
        *command,
    ])


def fixture_workflows(base_url: str) -> list[dict]:
    config = {
        "source_sheet_id": "sourceSheetFixture123",
        "publish_sheet_id": SHEET_ID,
        "publish_queue_tab": TAB,
        "publish_privacy": "공개",
        "publish_category_id": 22,
        "archive_folder_id": "archiveFolderFixture123",
        "google_credential_id": "fixtureGoogleCredential",
        "typecast_voice_id": "fixtureVoice",
    }
    workflows = render(PROJECT / "src", config)
    handoff = next(
        json.loads(json.dumps(item))
        for item in workflows if item["id"] == "SFAutomaticPublishQueueHandoff"
    )
    scheduled = {node["name"] for node in handoff["nodes"]
                 if node["type"] == "n8n-nodes-base.scheduleTrigger"}
    handoff["nodes"] = [node for node in handoff["nodes"] if node["name"] not in scheduled]
    handoff["connections"] = {
        source: targets for source, targets in handoff["connections"].items()
        if source not in scheduled
    }
    handoff["id"] = WORKFLOW_ID
    handoff["name"] = "Creator Queue Handoff E2E"
    for node in handoff["nodes"]:
        if node["type"] == "n8n-nodes-base.httpRequest":
            url = node["parameters"].get("url", "")
            node["parameters"]["url"] = url.replace(
                "http://shortform-gateway:8080", base_url
            ).replace("https://sheets.googleapis.com", base_url)
            node["parameters"]["authentication"] = "none"
            node.pop("credentials", None)
    return [*workflows, handoff]


def main() -> int:
    fixture_settings = {
        "source_sheet_id": "sourceSheetFixture123",
        "publish_sheet_id": SHEET_ID,
        "publish_queue_tab": TAB,
        "publish_privacy": "공개",
        "publish_category_id": 22,
        "archive_folder_id": "archiveFolderFixture123",
        "google_credential_id": "fixtureGoogleCredential",
        "typecast_voice_id": "fixtureVoice",
    }
    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server, tempfile.TemporaryDirectory() as temp:
        global QUEUE_ROWS, REQUESTS
        with LOCK:
            QUEUE_ROWS = []
            REQUESTS = []
        data_dir = Path(temp) / "n8n-data"
        data_dir.mkdir(mode=0o777)
        os.chmod(data_dir, 0o777)
        fixture_dir = Path(temp) / "fixtures"
        fixture_dir.mkdir()
        workflows = fixture_workflows(f"http://127.0.0.1:{server.server_address[1]}")
        workflow_dir = fixture_dir / "workflows"
        workflow_dir.mkdir()
        for workflow in workflows:
            workflow_path = workflow_dir / (workflow["id"] + ".json")
            workflow_path.write_text(json.dumps(workflow, ensure_ascii=False), encoding="utf-8")
        server_thread = Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        try:
            imported = execute_n8n(
                ["import:workflow", "--separate", "--input=/fixtures/workflows"], data_dir, fixture_dir
            )
            if imported.returncode:
                raise RuntimeError("n8n_import_failed:" + imported.stdout[-1000:] + imported.stderr[-1000:])
            executed = execute_n8n(
                ["execute", "--id=" + WORKFLOW_ID, "--rawOutput"], data_dir, fixture_dir
            )
            if executed.returncode:
                raise RuntimeError("n8n_execution_failed:" + executed.stdout[-2000:] + executed.stderr[-2000:])
            listed = execute_n8n(
                ["list:workflow", "--active=true"], data_dir, fixture_dir
            )
            if listed.returncode or listed.stdout.strip():
                raise RuntimeError("creator_workflow_activated_during_test")
            all_workflows = execute_n8n(
                ["list:workflow", "--active=false"], data_dir, fixture_dir
            )
            expected_ids = {workflow["id"] for workflow in workflows}
            listed_ids = {
                line.split("|", 1)[0].strip()
                for line in all_workflows.stdout.splitlines()
                if "|" in line
            }
            if all_workflows.returncode or not expected_ids <= listed_ids:
                raise RuntimeError("creator_workflow_import_incomplete:" + all_workflows.stdout[-1000:])
            with LOCK:
                requests = list(REQUESTS)
                rows = [list(row) for row in QUEUE_ROWS]
            if requests != ["claim", "sheets_read", "sheets_append", "sheets_read", "ack"]:
                raise RuntimeError("handoff_request_order_invalid:" + json.dumps(requests))
            if rows != [EXPECTED_ROW]:
                raise RuntimeError("handoff_row_invalid:" + json.dumps(rows, ensure_ascii=False))
            print(json.dumps({
                "state": "CREATOR_HANDOFF_E2E_SUCCESS",
                "n8n_import": imported.stdout.strip(),
                "workflows_imported": len(expected_ids),
                "requests": requests,
                "queue_rows": len(rows),
                "workflow_active": False,
                "youtube_requests": 0,
                "fixture_settings": fixture_settings,
            }, ensure_ascii=False))
            return 0
        finally:
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
