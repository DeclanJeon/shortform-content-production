"""Bounded asynchronous worker. Only installed adapters can report success."""
from __future__ import annotations
import hashlib, json, math, os, time, uuid, wave, traceback
from datetime import datetime
from zoneinfo import ZoneInfo
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import psycopg
from psycopg.rows import dict_row
from adapters import caption_style, render_ffmpeg, typecast_timestamp_tts as typecast
from adapters.render_manifest import assemble_render_manifest

API_BASE=os.environ["API_BASE_URL"].rstrip("/")
WORKER_TOKEN=os.environ["WORKER_TOKEN"]
DATABASE_URL=os.environ["DATABASE_URL"]
ARTIFACT_ROOT=Path(os.environ.get("ARTIFACT_ROOT","/data/artifacts"))
TYPECAST_VOICE_ID=os.environ.get("TYPECAST_VOICE_ID","")
TYPECAST_MODEL="ssfm-v30"

def db_connect(): return psycopg.connect(DATABASE_URL,row_factory=dict_row)
def call_api(path,body=None):
    data=None if body is None else json.dumps(body).encode()
    req=Request(API_BASE+path,data=data,headers={"Content-Type":"application/json","X-Worker-Token":WORKER_TOKEN},method="POST")
    with urlopen(req,timeout=20) as response: return json.loads(response.read())
def key_from_file():
    path=os.environ.get("TYPECAST_API_KEY_FILE","")
    if path and Path(path).is_file(): return Path(path).read_text().strip()
    return os.environ.get("TYPECAST_API_KEY","")
def reserve_provider_intent(job,provider,input_hash,units,cost):
    now=datetime.now(ZoneInfo("Asia/Seoul")); day=now.date(); month=now.strftime("%Y-%m")
    daily_units=int(os.environ.get("TYPECAST_DAILY_CHARACTER_LIMIT","0"))
    monthly_units=int(os.environ.get("TYPECAST_MONTHLY_CHARACTER_LIMIT","0"))
    max_requests=int(os.environ.get("MAX_TTS_REQUESTS","0"))
    daily_budget=float(os.environ.get("DAILY_COST_LIMIT_USD","0"))
    episode_budget=float(os.environ.get("EPISODE_COST_LIMIT_USD","0"))
    if not (1<=units<=2000 and daily_units>0 and monthly_units>0 and max_requests>0 and cost>=0 and daily_budget>0 and episode_budget>0):
        return "BUDGET_UNCONFIGURED",None
    with db_connect() as db:
        db.execute("SELECT pg_advisory_xact_lock(hashtext(%s))",("provider-budget:typecast",))
        existing=db.execute("SELECT request_sha256,status,result FROM provider_call_intents WHERE job_id=%s AND provider=%s FOR UPDATE",(job["job_id"],provider)).fetchone()
        if existing:
            if existing["request_sha256"]!=input_hash: return "CONFLICT",None
            if existing["status"]=="SUCCEEDED": return "CACHED",existing["result"]
            return "UNCERTAIN",None
        dayrow=db.execute("SELECT COALESCE(sum(units),0) units,COALESCE(sum(reserved_cost),0) cost,COALESCE(sum(request_count),0) requests FROM provider_budget_reservations WHERE provider=%s AND usage_date=%s AND status<>'RELEASED'",(provider,day)).fetchone()
        month_units=db.execute("SELECT COALESCE(sum(units),0) AS units FROM provider_budget_reservations WHERE provider=%s AND usage_month=%s AND status<>'RELEASED'",(provider,month)).fetchone()["units"]
        runrow=db.execute("SELECT COALESCE(sum(reserved_cost),0) cost,COALESCE(sum(request_count),0) requests FROM provider_budget_reservations WHERE provider=%s AND run_id=%s AND status<>'RELEASED'",(provider,job["run_id"])).fetchone()
        if dayrow["units"]+units>daily_units or month_units+units>monthly_units or dayrow["requests"]+1>max_requests:
            return "QUOTA_EXHAUSTED",None
        if float(dayrow["cost"])+cost>daily_budget or float(runrow["cost"])+cost>episode_budget or runrow["requests"]+1>max_requests:
            return "BUDGET_EXHAUSTED",None
        db.execute("INSERT INTO provider_budget_reservations(job_id,provider,run_id,usage_date,usage_month,units,reserved_cost) VALUES (%s,%s,%s,%s,%s,%s,%s)",(job["job_id"],provider,job["run_id"],day,month,units,cost))
        db.execute("INSERT INTO provider_call_intents(job_id,provider,request_sha256,status) VALUES (%s,%s,%s,'IN_PROGRESS')",(job["job_id"],provider,input_hash))
    return "NEW",None

def finish_intent(job_id,provider,status,result):
    with db_connect() as db:
        db.execute("UPDATE provider_call_intents SET status=%s,result=%s,updated_at=now() WHERE job_id=%s AND provider=%s",
                   (status,json.dumps(result),job_id,provider))
        if status=="SUCCEEDED": db.execute("UPDATE provider_budget_reservations SET status='COMPLETED' WHERE job_id=%s AND provider=%s",(job_id,provider))

def write_tts(job):
    payload=job["request"].get("payload",{})
    text=payload.get("text")
    if not isinstance(text,str) or not text.strip() or len(text)>2000:
        return "needs_review",{}, {"code":"tts_text_invalid"}
    key=key_from_file()
    if not key or not TYPECAST_VOICE_ID:
        return "needs_review",{}, {"code":"typecast_not_configured"}
    provider_payload={"text":text,"voice_id":TYPECAST_VOICE_ID,"model":TYPECAST_MODEL,
                      "emotion_preset":payload.get("emotion_preset","normal"),
                      "emotion_intensity":payload.get("emotion_intensity",1.0),"tempo":payload.get("tempo",1.0)}
    input_hash=hashlib.sha256(json.dumps(provider_payload,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    cost=float(os.environ.get("TYPECAST_REQUEST_COST_BOUND_USD","-1"))
    intent,cached=reserve_provider_intent(job,"typecast",input_hash,len(text),cost)
    if intent=="CACHED": return "succeeded",cached,None
    if intent!="NEW": return "needs_review",{}, {"code":"provider_outcome_uncertain" if intent=="UNCERTAIN" else intent.lower()}
    try:
        result=typecast.synthesize_timestamped(text,TYPECAST_VOICE_ID,key,allow_paid_generation=True,
                 emotion_preset=provider_payload["emotion_preset"],emotion_intensity=provider_payload["emotion_intensity"],tempo=provider_payload["tempo"])
        if result["audio_format"]=="wav":
            with wave.open(BytesIO(result["audio_bytes"]),"rb") as wav: measured=wav.getnframes()/float(wav.getframerate())
        else: measured=float(result["duration_seconds"])
        if abs(measured-float(result["duration_seconds"]))>0.15:
            raise ValueError("provider_duration_mismatch")
        base=ARTIFACT_ROOT/job["job_id"];base.mkdir(parents=True,exist_ok=True,mode=0o700)
        ext=result["audio_format"];audio_path=base/f"voice.{ext}"
        temp=base/(audio_path.name+".tmp");temp.write_bytes(result["audio_bytes"]);os.chmod(temp,0o600);temp.replace(audio_path)
        digest=hashlib.sha256(result["audio_bytes"]).hexdigest()
        manifest={"job_id":job["job_id"],"provider":"Typecast","model":TYPECAST_MODEL,"audio_format":ext,
                  "duration_seconds":round(measured,3),"character_count":len(text),"caption_cue_count":len(result["captions"]),
                  "sha256":digest,"artifact_path":str(audio_path),"captions":result["captions"],"archive_status":"PENDING_DRIVE_SYNC",
                  **({"word_timestamps":result["word_timestamps"]} if "word_timestamps" in result else {})}
        meta=base/"tts-manifest.json";meta.write_text(json.dumps({**manifest,"captions":result["captions"]},ensure_ascii=False,indent=2)+"\n");os.chmod(meta,0o600)
        finish_intent(job["job_id"],"typecast","SUCCEEDED",manifest)
        return "succeeded",manifest,None
    except (HTTPError,URLError,TimeoutError) as exc:
        # An uncertain vendor result is never automatically billed a second time.
        finish_intent(job["job_id"],"typecast","UNCERTAIN",{"error_class":type(exc).__name__})
        return "needs_review",{}, {"code":"typecast_outcome_uncertain","error_class":type(exc).__name__}
    except Exception as exc:
        finish_intent(job["job_id"],"typecast","UNCERTAIN",{"error_class":type(exc).__name__})
        return "needs_review",{}, {"code":"typecast_failed_or_uncertain","error_class":type(exc).__name__}

def _valid_render_result(result, job_id, style):
    if not isinstance(result, dict) or result.get("job_id") != job_id:
        return False
    if (result.get("video_codec") != "h264" or result.get("audio_codec") != "aac"
            or (result.get("width"), result.get("height")) != (1080, 1920)
            or result.get("caption_style_id") != style["preset_id"]):
        return False
    size = result.get("size_bytes")
    duration = result.get("duration_seconds")
    digest = result.get("sha256")
    cues = result.get("cues")
    if (isinstance(size, bool) or not isinstance(size, int) or size <= 0
            or isinstance(duration, bool) or not isinstance(duration, (int, float))
            or not math.isfinite(duration) or duration <= 0
            or not isinstance(digest, str) or len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest)
            or not isinstance(cues, list)):
        return False
    try:
        root = ARTIFACT_ROOT.resolve(strict=True)
        for key in ("artifact_path", "manifest_path"):
            path = Path(result[key]).resolve(strict=True)
            path.relative_to(root)
            if not path.is_file() or path.stat().st_size == 0:
                return False
        artifact = Path(result["artifact_path"]).resolve(strict=True)
        if artifact.stat().st_size != size:
            return False
    except (KeyError, OSError, TypeError, ValueError):
        return False
    return True


def process(job):
    stage=job["stage"]
    if stage=="tts": return write_tts(job)
    if stage=="render":
        request=job.get("request")
        payload=request.get("payload") if isinstance(request, dict) else None
        if not isinstance(payload, dict):
            return "needs_review",{}, {"code":"render_manifest_invalid"}
        try:
            style=caption_style.validate_render_caption_style(payload.get("caption_style"))
        except ValueError:
            return "needs_review",{}, {"code":"render_caption_style_invalid"}
        manifest=payload.get("render_manifest")
        if manifest is None and "shot_sequence" in payload and "tts_result" in payload:
            try:
                manifest=assemble_render_manifest(payload["shot_sequence"],payload["tts_result"],artifact_root=ARTIFACT_ROOT)
            except Exception:
                return "needs_review",{}, {"code":"render_handoff_invalid"}
        if not isinstance(manifest, dict):
            return "needs_review",{}, {"code":"render_manifest_invalid"}
        job_id=job.get("job_id")
        if not isinstance(job_id, str):
            return "needs_review",{}, {"code":"render_job_id_invalid"}
        if "job_id" in manifest:
            return "needs_review",{}, {"code":"render_job_id_forbidden"}
        nested_style=manifest.get("caption_style")
        if "caption_style" in manifest and nested_style != style:
            return "needs_review",{}, {"code":"render_caption_style_mismatch"}
        render_manifest={**manifest,"job_id":job_id,"caption_style":style}
        try:
            result=render_ffmpeg.render(render_manifest, artifact_root=ARTIFACT_ROOT)
        except Exception:
            return "needs_review",{}, {"code":"render_failed"}
        if not _valid_render_result(result, job_id, style):
            return "needs_review",{}, {"code":"render_result_invalid"}
        return "succeeded",result,None
    # Unsupported adapters stay visibly blocked; never manufacture a successful artifact.
    return "needs_review",{}, {"code":"worker_adapter_not_configured","stage":stage}

def main():
    while True:
        try:
            response=call_api("/workers/claim",{'stages':['tts','render']})
            job=response.get("job")
            if not job:
                time.sleep(float(os.environ.get("POLL_SECONDS","2")));continue
            status,result,error=process(job)
            completion={"status":status,"result":result,"error":error,
                        "event_id":f"worker:{job['job_id']}:{job['attempt']}:{status}"}
            call_api(f"/workers/{job['job_id']}/complete",completion)
            print(json.dumps({"job_id":job["job_id"],"stage":job["stage"],"status":status,"error_code":(error or {}).get("code")}),flush=True)
        except HTTPError as exc:
            print(json.dumps({"worker_error":"gateway_http","status":exc.code}),flush=True);time.sleep(3)
        except Exception as exc:
            print(json.dumps({"worker_error":type(exc).__name__,"trace":traceback.format_exc()[-2000:]}),flush=True);time.sleep(3)

if __name__=="__main__":main()
