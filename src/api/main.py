"""Authenticated job gateway for the Shortform n8n/worker system.

The gateway persists work; only explicitly installed worker adapters can complete it.
Unsupported work is reported as needs_review, never reported as a fabricated success.
"""
from __future__ import annotations
import hashlib, hmac, json, os, uuid
from datetime import date, datetime, timezone
import math
import re
from pathlib import Path
from typing import Any
import psycopg
from fastapi import FastAPI, Header, HTTPException
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict, Field
from artifacts import create_artifacts_router
from automatic_archive import create_automatic_archive_router
from image_budget import create_image_budget_router
from production_policy import create_production_policy_router
from project_bundle import create_project_bundle_router
from production_projection import create_production_projection_router
from publish_handoff import create_publish_handoff_router
from sheet_intake import create_sheet_intake_router
from creative_automation import create_creative_router
from media_automation import create_media_router
from review_callback import (
    ReviewDatabase,
    has_active_production_authorization,
)
from review_callback_router import create_review_callback_router

DATABASE_URL = os.environ["DATABASE_URL"]
API_TOKEN = os.environ["API_TOKEN"]
WORKER_TOKEN = os.environ["WORKER_TOKEN"]
app = FastAPI(title="Shortform Production Gateway", version="1.1.0")

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")

ROUTE_STAGES = {
    "collect": "collect", "story": "story", "script": "script",
    "characters": "character_image", "scenes": "scene_image", "tts": "tts",
    "render": "render", "archive": "drive_sync", "recovery": "recovery",
}
STAGES = set(ROUTE_STAGES.values()) | {"seed", "pitches", "ssot", "motion", "timeline", "qa"}
PRODUCTION_GATED_STAGES = {"character_image", "scene_image", "motion", "tts", "timeline", "render"}

class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_date: date
    target_count: int = Field(default=12, ge=1, le=12)
    request_id: str = Field(min_length=8, max_length=128)

class JobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=8, max_length=128)
    run_id: uuid.UUID
    story_id: str | None = None
    stage: str | None = None
    idempotency_key: str = Field(min_length=8, max_length=200)
    schema_version: str = "1.0"
    payload: dict[str, Any]
    max_attempts: int = Field(default=3, ge=1, le=10)

class WorkerClaim(BaseModel):
    model_config = ConfigDict(extra='forbid')
    stages: list[str] = Field(default_factory=lambda:['tts'], min_length=1, max_length=2)

class Completion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: str
    result: dict[str, Any] = Field(default_factory=dict)
    error: dict[str, Any] | None = None
    event_id: str = Field(min_length=8, max_length=200)

def authorized(value: str | None, expected: str, scheme: str = "Bearer ") -> None:
    if not value or not hmac.compare_digest(value.removeprefix(scheme), expected):
        raise HTTPException(401, "unauthorized")

def connection():
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)

app.include_router(create_production_policy_router(connection,API_TOKEN,
    lambda:os.environ.get('AUTOMATIC_POLICY_APPROVAL_ENABLED','false').lower()=='true'))
app.include_router(create_image_budget_router(connection, API_TOKEN,
    lambda: os.environ.get('AUTOMATIC_MEDIA_ENABLED','false').lower()=='true',
    lambda: {'per_image_usd':os.environ.get('IMAGE_RESERVATION_USD','0.25'),
             'daily_usd':os.environ.get('IMAGE_DAILY_RESERVATION_USD','120.00'),
             'episode_usd':os.environ.get('IMAGE_EPISODE_RESERVATION_USD','10.00'),
             'daily_calls':os.environ.get('IMAGE_DAILY_CALL_LIMIT','480')}))
app.include_router(create_production_projection_router(connection,API_TOKEN))
app.include_router(create_publish_handoff_router(
    connection,
    API_TOKEN,
    os.environ.get('PUBLISH_SHEET_ID',''),
    os.environ.get('PUBLISH_QUEUE_TAB','발행 대기'),
    os.environ.get('PUBLISH_PRIVACY','공개'),
    os.environ.get('PUBLISH_CATEGORY_ID','22'),
))
app.include_router(create_project_bundle_router(connection,API_TOKEN,os.environ.get('ARTIFACT_ROOT','/data/artifacts')))
app.include_router(create_automatic_archive_router(connection,API_TOKEN,os.environ.get('AUTOMATIC_ARCHIVE_FOLDER_ID',''),kind='project'))
app.include_router(create_artifacts_router(connection, API_TOKEN))
app.include_router(create_automatic_archive_router(
    connection, API_TOKEN, os.environ.get("AUTOMATIC_ARCHIVE_FOLDER_ID", ""),
))
app.include_router(create_sheet_intake_router(connection, API_TOKEN, lambda: datetime.now(timezone.utc),
    lambda: os.environ.get('AUTOMATIC_CREATIVE_ENABLED','false').lower()=='true'))
app.include_router(create_creative_router(
    connection, API_TOKEN, lambda: datetime.now(timezone.utc),
    lambda: os.environ.get('AUTOMATIC_CREATIVE_ENABLED', 'false').lower() == 'true',
    lambda: int(os.environ.get('AUTOMATIC_CREATIVE_DAILY_LIMIT', '12')),
    lambda: os.environ.get('CREATIVE_SKILL_REVISION', ''),
    lambda: int(os.environ.get('PLANNING_BUFFER_TARGET','0')),
    lambda: os.environ.get('ALLOW_SHEET_INSPIRATION','false').lower()=='true',
))

app.include_router(create_media_router(connection, API_TOKEN,
    lambda: os.environ.get('AUTOMATIC_CREATIVE_ENABLED','false').lower()=='true' and os.environ.get('AUTOMATIC_MEDIA_ENABLED','false').lower()=='true',
    os.environ.get('ARTIFACT_ROOT','/data/artifacts'),
    lambda: int(os.environ.get('AUTOMATIC_CREATIVE_DAILY_LIMIT','12')),
    clock=lambda:datetime.now(timezone.utc),
    interval_minutes=lambda:int(os.environ.get('PRODUCTION_INTERVAL_MINUTES','120')),
    start_at=lambda:os.environ.get('PRODUCTION_START_AT','')))
app.include_router(create_review_callback_router(
    connection,
    lambda: Path(os.environ.get("REVIEW_CALLBACK_SECRET_FILE", "")),
    lambda: os.environ.get("REVIEW_CALLBACK_REVIEWER_ID", ""),
    lambda: int(datetime.now(timezone.utc).timestamp()),
    lambda: int(os.environ.get("REVIEW_CALLBACK_MAX_SKEW_SECONDS", "300")),
))

def enqueue(body: JobRequest):
    if body.stage not in STAGES:
        raise HTTPException(422, "unsupported stage")
    if body.stage in PRODUCTION_GATED_STAGES:
        if not body.story_id:
            raise HTTPException(409, "production_approval_required")
        with connection() as db:
            if not has_active_production_authorization(db, body.story_id):
                raise HTTPException(409, "production_approval_required")
    identity={"payload":body.payload,"run_id":str(body.run_id),"story_id":body.story_id,"stage":body.stage}
    raw=json.dumps(identity,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()
    digest=hashlib.sha256(raw).hexdigest()
    with connection() as db:
        row = db.execute("""INSERT INTO generation_jobs(run_id,story_id,stage,status,input_sha256,idempotency_key,max_attempts,request)
          VALUES (%s,%s,%s,'queued',%s,%s,%s,%s)
          ON CONFLICT(idempotency_key) DO UPDATE SET idempotency_key=EXCLUDED.idempotency_key
          RETURNING id,run_id,stage,status,input_sha256,idempotency_key,attempt""",
          (body.run_id,body.story_id,body.stage,digest,body.idempotency_key,body.max_attempts,body.model_dump_json())).fetchone()
    if row["input_sha256"]!=digest or row["stage"]!=body.stage:
        raise HTTPException(409,"idempotency_key_payload_conflict")
    return {"job_id":str(row["id"]),"run_id":str(row["run_id"]),"request_id":body.request_id,"idempotency_key":row["idempotency_key"],"stage":row["stage"],"status":row["status"],"attempt":row["attempt"],"input_sha256":row["input_sha256"],"status_url":f"/v1/jobs/{row['id']}"}

@app.get("/v1/healthz")
def health():
    try:
        with connection() as db:
            db.execute("SELECT 1")
            schema_ok=db.execute("SELECT to_regclass('public.generation_jobs') IS NOT NULL AS ready").fetchone()["ready"]
        if not schema_ok: raise HTTPException(503,"database schema not initialized")
        return {"status":"ok","database":"ok","queue":"ready","gateway_version":"1.1.0"}
    except Exception:
        raise HTTPException(503,"database unavailable")

@app.post("/v1/runs/start",status_code=201)
def start_run(body:RunRequest,authorization:str|None=Header(default=None)):
    authorized(authorization,API_TOKEN)
    with connection() as db:
        row=db.execute("""INSERT INTO runs(run_date,status,target_count,metrics)
          VALUES (%s,'RUNNING',%s,jsonb_build_object('request_id',%s::text))
          ON CONFLICT(run_date) DO UPDATE SET run_date=EXCLUDED.run_date
          RETURNING id,run_date,status,target_count""",(body.run_date,body.target_count,body.request_id)).fetchone()
    return {"run_id":str(row["id"]),"run_date":str(row["run_date"]),"status":row["status"],"target_count":row["target_count"]}

@app.post("/v1/jobs",status_code=202)
def create_job(body:JobRequest,authorization:str|None=Header(default=None)):
    authorized(authorization,API_TOKEN)
    return enqueue(body)

@app.post("/v1/pipeline/{route}",status_code=202)
def dispatch_pipeline(route:str,body:JobRequest,authorization:str|None=Header(default=None)):
    authorized(authorization,API_TOKEN)
    stage=ROUTE_STAGES.get(route)
    if stage is None: raise HTTPException(404,"unknown pipeline route")
    return enqueue(body.model_copy(update={"stage":stage}))

@app.get("/v1/jobs/{job_id}")
def get_job(job_id:uuid.UUID,authorization:str|None=Header(default=None)):
    authorized(authorization,API_TOKEN)
    with connection() as db:
        row=db.execute("SELECT id,run_id,story_id,stage,status,attempt,max_attempts,result,error,created_at,updated_at FROM generation_jobs WHERE id=%s",(job_id,)).fetchone()
    if row is None: raise HTTPException(404,"job not found")
    row = {**row, "job_id": row["id"]}
    result = row.get("result")
    if row.get("stage") == "render" and row.get("status") == "succeeded":
        artifact = None
        if isinstance(result, dict):
            sha256 = result.get("sha256")
            size = result.get("size_bytes")
            codec = result.get("video_codec")
            duration = result.get("duration_seconds")
            artifact_path = result.get("artifact_path")
            if (result.get("job_id") == str(row["id"])
                and isinstance(artifact_path, str) and bool(artifact_path)
                and (_SHA256.fullmatch(sha256) if isinstance(sha256, str) else False) and (
                isinstance(size, int) and not isinstance(size, bool) and size > 0
                and codec == "h264"
                and isinstance(duration, (int, float)) and not isinstance(duration, bool)
                and (isinstance(duration, int) or math.isfinite(duration)) and 0 < duration <= 180
            )):
                artifact = {"checksum_algorithm":"sha256", "sha256":sha256,
                            "checksum_evidence_sha256":sha256, "size_bytes":size,
                            "codec":codec, "duration_seconds":duration}
        row = {**row, "job_id":row["id"], "result": {}, "artifact": artifact}
    return {k:(v.isoformat() if isinstance(v,datetime) else str(v) if isinstance(v,uuid.UUID) else v) for k,v in row.items()}

@app.post("/v1/workers/claim")
def claim_job(body:WorkerClaim|None=None,x_worker_token:str|None=Header(default=None)):
    authorized(x_worker_token,WORKER_TOKEN,scheme="")
    stages=(body or WorkerClaim()).stages
    if len(set(stages))!=len(stages) or not set(stages)<={'tts','render'}:raise HTTPException(422,'unsupported_worker_capability')
    with connection() as db:
        stale=db.execute("""UPDATE generation_jobs SET
          status=CASE WHEN attempt>=max_attempts THEN 'failed' ELSE 'queued' END,
          error=jsonb_build_object('code','worker_lease_expired'),updated_at=now()
          WHERE status='running' AND updated_at<now()-interval '10 minutes'
          RETURNING id,attempt,status""").fetchall()
        for expired in stale:
            db.execute("INSERT INTO job_events(job_id,event_key,from_status,to_status,payload) VALUES (%s,%s,'running',%s,%s) ON CONFLICT(event_key) DO NOTHING",
                       (expired["id"],f"lease-expired:{expired['id']}:{expired['attempt']}",expired["status"],json.dumps({"code":"worker_lease_expired"})))
        row=db.execute("""SELECT id,run_id,story_id,stage,attempt,max_attempts,idempotency_key,request
          FROM generation_jobs WHERE status='queued' AND (next_retry_at IS NULL OR next_retry_at<=now())
          AND attempt<max_attempts AND stage=ANY(%s)
          AND (stage <> ALL(%s) OR (story_id IS NOT NULL AND EXISTS (
            SELECT 1 FROM outbox o WHERE o.event_type='creative_plan_ready'
              AND o.aggregate_id=generation_jobs.story_id
              AND o.status IN ('PENDING','MEDIA_RUNNING','PRIVATE_VIDEO_READY')
              AND o.payload->>'production_authorized'='true'
              AND o.payload->>'publication_authorized'='false'
              AND (o.payload->>'plan_sha256')=(o.payload->'production_approval'->>'plan_sha256')
          )))
          ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1""",(stages,sorted(PRODUCTION_GATED_STAGES))).fetchone()
        if row is None: return {"job":None}
        attempt=row["attempt"]+1
        db.execute("UPDATE generation_jobs SET status='running',attempt=%s,error=NULL,updated_at=now() WHERE id=%s",(attempt,row["id"]))
        db.execute("INSERT INTO job_events(job_id,event_key,from_status,to_status,payload) VALUES (%s,%s,'queued','running',%s) ON CONFLICT(event_key) DO NOTHING",
                   (row["id"],f"claim:{row['idempotency_key']}:{attempt}",json.dumps({"attempt":attempt})))
    envelope=row["request"] if isinstance(row["request"],dict) else json.loads(row["request"])
    return {"job":{"job_id":str(row["id"]),"run_id":str(row["run_id"]),"story_id":row["story_id"],"stage":row["stage"],"attempt":attempt,"max_attempts":row["max_attempts"],"idempotency_key":row["idempotency_key"],"request":envelope}}

@app.post("/v1/workers/{job_id}/complete")
def complete_job(job_id:uuid.UUID,body:Completion,x_worker_token:str|None=Header(default=None)):
    authorized(x_worker_token,WORKER_TOKEN,scheme="")
    if body.status not in {"succeeded","failed","needs_review"}: raise HTTPException(422,"invalid terminal status")
    if body.status=="succeeded" and not body.result: raise HTTPException(422,"success requires result evidence")
    with connection() as db:
        row=db.execute("SELECT status FROM generation_jobs WHERE id=%s FOR UPDATE",(job_id,)).fetchone()
        if row is None: raise HTTPException(404,"job not found")
        if row["status"]!="running": raise HTTPException(409,"job is not running")
        db.execute("UPDATE generation_jobs SET status=%s,result=%s,error=%s,updated_at=now() WHERE id=%s",
                   (body.status,json.dumps(body.result),json.dumps(body.error) if body.error else None,job_id))
        db.execute("INSERT INTO job_events(job_id,event_key,from_status,to_status,payload) VALUES (%s,%s,'running',%s,%s) ON CONFLICT(event_key) DO NOTHING",
                   (job_id,body.event_id,body.status,json.dumps({"result":body.result,"error":body.error})))
    return {"job_id":str(job_id),"status":body.status}

