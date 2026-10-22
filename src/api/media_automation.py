"""Claim the validated creative-plan handoff and attest actual private rendering.

No publication authority. Pending provider outcomes never automatically requeue.
"""
import hashlib
import copy
import hmac
import json
import math
from pathlib import Path
import re
import uuid
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from fastapi import APIRouter,Header,HTTPException,Request
from creative.publication_rights import effective_rights, qa08
from creative.cadence import production_window
from creative.plan_revision import story_version


def create_media_router(connection_factory,api_token,enabled,artifact_root,daily_limit=lambda:12,*,
                        clock=lambda:datetime.now(timezone.utc),interval_minutes=lambda:120,start_at=lambda:''):
    router=APIRouter(prefix='/v1/automation')
    def auth(s):
        try:valid=isinstance(s,str) and s.startswith('Bearer ') and hmac.compare_digest(s[7:].encode('ascii'),api_token.encode('ascii'))
        except UnicodeEncodeError:valid=False
        if not valid:raise HTTPException(401,'unauthorized')
    async def body(request,keys):
        raw=bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw)>512*1024:raise HTTPException(413,'request_too_large')
        try:b=json.loads(raw)
        except Exception:raise HTTPException(422,'invalid_body') from None
        if not isinstance(b,dict) or set(b)!=set(keys):raise HTTPException(422,'invalid_body')
        return b
    def task(db,mid):
        row=db.execute("SELECT id,status,payload FROM outbox WHERE id=%s AND event_type='creative_plan_ready' FOR UPDATE",(mid,)).fetchone()
        if not row:raise HTTPException(404,'media_task_not_found')
        p=row['payload'];source=db.execute("SELECT id,status,payload FROM outbox WHERE id=%s AND event_type='creative_production' FOR UPDATE",(int(p['task_id']),)).fetchone()
        if not source or source['payload'].get('plan_sha256')!=p['plan_sha256']:raise HTTPException(409,'media_plan_lineage_invalid')
        rights=db.execute("SELECT rights_state,evidence FROM sources WHERE id=%s FOR UPDATE",(source['payload']['source_key'],)).fetchone()
        rights_state,rights_evidence=effective_rights(source['payload'],rights)
        source_payload={**source['payload'],'rights_state':rights_state,'rights_evidence':rights_evidence}
        approval=p.get('production_approval')
        if p.get('production_authorized') is not True or not isinstance(approval,dict) or approval.get('decision')!='approve' or approval.get('plan_sha256')!=p['plan_sha256']:
            raise HTTPException(409,'production_approval_required')
        return row,p,source,source_payload
    def envelope(row,p,source):return {**p,'media_intent_id':str(row['id']),'state':row['status'],'plan':source['plan'],'plan_review':source.get('review'),'plan_provenance':source.get('provenance'),'source_key':source['source_key'],'source_date':source['source_date'],'rights_state':source.get('rights_state','UNVERIFIED'),'rights_evidence':source.get('rights_evidence',{}),'publication_authorized':False}

    @router.get('/media-status')
    def status(authorization:str|None=Header(default=None)):
        auth(authorization)
        return {'media_enabled':bool(enabled()),'daily_media_limit':daily_limit(),
                'production_interval_minutes':interval_minutes(),'production_start_at':start_at(),
                'missed_slots_catch_up':False}

    @router.post('/claim-media')
    async def claim(request:Request,authorization:str|None=Header(default=None)):
        auth(authorization);await body(request,set())
        if not enabled():return {'task':None,'media_enabled':False}
        try:
            with connection_factory() as db:
                now=clock();period=interval_minutes();window=production_window(now,period,start_at())
                today=now.astimezone(ZoneInfo('Asia/Seoul')).date().isoformat()
                limit=daily_limit()
                if type(limit) is not int or not 1<=limit<=12:raise HTTPException(503,'media_cadence_unconfigured')
                # One lock spans midnight too; different day locks permit a race.
                db.execute('SELECT pg_advisory_xact_lock(hashtext(%s))',('media-production-cadence',))
                used=db.execute("SELECT count(*) AS n FROM outbox WHERE event_type='creative_plan_ready' AND payload->>'media_claim_date'=%s",(today,)).fetchone()['n']
                if used>=limit:return {'task':None,'media_enabled':True,'reason':'daily_media_limit'}
                latest=db.execute("SELECT payload->>'media_claimed_at' AS claimed_at FROM outbox WHERE event_type='creative_plan_ready' AND payload ? 'media_claimed_at' ORDER BY (payload->>'media_claimed_at')::timestamptz DESC LIMIT 1").fetchone()
                next_allowed=(datetime.fromisoformat(latest['claimed_at'])+timedelta(minutes=period)) if latest else window.starts_at
                occupied=db.execute("SELECT 1 FROM outbox WHERE event_type='creative_plan_ready' AND payload->>'media_claim_slot'=%s LIMIT 1",(window.key,)).fetchone()
                if not window.admitted_time or occupied or now<next_allowed:
                    next_due=max(next_allowed,window.next_at if occupied else window.starts_at)
                    return {'task':None,'media_enabled':True,'reason':'production_interval_not_due','next_eligible_at':next_due.isoformat()} 
                db.execute("UPDATE outbox SET status='NEEDS_REVIEW',payload=payload||%s::jsonb WHERE event_type='creative_plan_ready' AND status='MEDIA_RUNNING' AND next_attempt_at<now()",(json.dumps({'error_code':'media_outcome_uncertain'}),))
                r=db.execute("""SELECT id,status,payload FROM outbox
                    WHERE event_type='creative_plan_ready' AND status='PENDING'
                      AND payload->>'production_authorized'='true'
                      AND payload->'production_approval'->>'decision'='approve'
                      AND (payload->'production_approval'->>'plan_sha256')=(payload->>'plan_sha256')
                    ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1""").fetchone()
                if not r:return {'task':None,'media_enabled':True}
                row,p,_,source=task(db,r['id']);p['media_claim_id']=str(uuid.uuid4());p['media_claim_date']=today
                p['media_claim_slot']=window.key;p['media_claimed_at']=now.astimezone(timezone.utc).isoformat()
                db.execute("UPDATE outbox SET status='MEDIA_RUNNING',payload=%s,attempts=attempts+1,next_attempt_at=now()+interval '4 hours' WHERE id=%s",(json.dumps(p),row['id']))
                db.execute("UPDATE stories SET status='MEDIA_RUNNING' WHERE id=%s",(p['story_id'],))
                row={**row,'status':'MEDIA_RUNNING'}
            return {'task':envelope(row,p,source),'media_enabled':True}
        except HTTPException:raise
        except Exception:raise HTTPException(503,'media_claim_unavailable') from None

    @router.get('/media-tasks/{mid}')
    def get(mid:int,authorization:str|None=Header(default=None)):
        auth(authorization)
        try:
            with connection_factory() as db:row,p,_,source=task(db,mid)
            return envelope(row,p,source)
        except HTTPException:raise
        except Exception:raise HTTPException(503,'media_task_unavailable') from None

    @router.post('/media-tasks/{mid}/complete')
    async def complete(mid:int,request:Request,authorization:str|None=Header(default=None)):
        auth(authorization);b=await body(request,{'media_claim_id','result'});result=b['result']
        if not isinstance(result,dict) or result.get('state')!='PRIVATE_VIDEO_READY' or result.get('publication_authorized') is not False or result.get('human_review')!='NOT_PERFORMED' or result.get('visual_review_kind')!='AI_VISUAL_REVIEW' or result.get('final_render_visual_review')!='AI_SAMPLED_FRAMES_PASS':raise HTTPException(422,'media_qa_incomplete')
        qa_checks=result.get('qa_checks')
        required_qa={f'QA-0{i}' for i in range(1,9)}
        if (not isinstance(qa_checks,list) or len(qa_checks)!=8
                or {check.get('check_id') for check in qa_checks if isinstance(check,dict)}!=required_qa
                or any(not isinstance(check,dict) or check.get('status') not in ('PASS','FAIL','NEEDS_REVIEW')
                    or not isinstance(check.get('evidence_artifact_ids'),list) or not check['evidence_artifact_ids']
                    or any(not isinstance(e,str) or not e for e in check['evidence_artifact_ids'])
                    or not isinstance(check.get('checker_version'),str) or not check['checker_version']
                    for check in qa_checks)):
            raise HTTPException(422,'media_qa_evidence_invalid')
        if not isinstance(result.get('sha256'),str) or not re.fullmatch(r'[0-9a-f]{64}',result['sha256']) or not isinstance(result.get('md5'),str) or not re.fullmatch(r'[0-9a-f]{32}',result['md5']):raise HTTPException(422,'media_checksum_invalid')
        try:render_id=uuid.UUID(result['render_job_id']);tts_id=uuid.UUID(result['tts_job_id'])
        except (ValueError,TypeError,KeyError):raise HTTPException(422,'media_job_identity_invalid') from None
        try:
            with connection_factory() as db:
                row,p,parent,source=task(db,mid)
                narrative_profile=(source['plan'].get('schema_version')=='2.0'
                                   or (source['plan'].get('schema_version')=='3.0'
                                       and isinstance((source.get('provenance') or {}).get('narrative_review'),dict)))
                if narrative_profile:
                    from creative.narrative import validate_visual_proof
                    try:validate_visual_proof(source['plan'],source.get('provenance') or {},result)
                    except (ValueError,TypeError,KeyError):raise HTTPException(422,'media_narrative_evidence_invalid') from None
                if source.get('content_policy'):
                    result=copy.deepcopy(result)
                    result['qa_checks']=[qa08(source) if q['check_id']=='QA-08' else q for q in result['qa_checks']]
                    qa_checks=result['qa_checks']
                if not isinstance(b['media_claim_id'],str) or b['media_claim_id']!=p.get('media_claim_id'):raise HTTPException(409,'media_claim_conflict')
                if p.get('production_authorized') is not True or p.get('production_approval',{}).get('plan_sha256')!=p.get('plan_sha256'):raise HTTPException(409,'production_approval_required')
                if row['status']=='PRIVATE_VIDEO_READY' and p.get('result')==result:return {'state':'PRIVATE_VIDEO_READY','media_intent_id':str(mid),'youtube_upload':False,'result':result}
                if row['status']!='MEDIA_RUNNING':raise HTTPException(409,'media_state_conflict')
                render=db.execute("SELECT run_id,story_id,status,result FROM generation_jobs WHERE id=%s AND stage='render'",(render_id,)).fetchone();tts=db.execute("SELECT run_id,story_id,status FROM generation_jobs WHERE id=%s AND stage='tts'",(tts_id,)).fetchone()
                if not render or not tts or any(str(j['run_id'])!=p['run_id'] or j['story_id']!=p['story_id'] or j['status']!='succeeded' for j in (render,tts)):raise HTTPException(409,'media_job_lineage_invalid')
                native=render['result'];duration=native.get('duration_seconds');size=native.get('size_bytes')
                if native.get('job_id')!=str(render_id) or native.get('video_codec')!='h264' or native.get('audio_codec')!='aac' or (native.get('width'),native.get('height'))!=(1080,1920) or type(duration) not in (int,float) or not math.isfinite(duration) or not 0<duration<=180 or native.get('sha256')!=result['sha256'] or size!=result.get('size_bytes') or duration!=result.get('duration_seconds'):raise HTTPException(409,'native_render_metadata_mismatch')
                hashes=result.get('scene_hashes')
                if not isinstance(hashes,list) or len(hashes)!=len(source['plan']['shots']) or len(set(hashes))!=len(hashes) or any(not isinstance(h,str) or not re.fullmatch(r'[0-9a-f]{64}',h) for h in hashes):raise HTTPException(422,'media_unique_scenes_invalid')
                root=Path(artifact_root).resolve(strict=True);path=Path(native['artifact_path']).resolve(strict=True)
                if not path.is_relative_to(root) or not path.is_file():raise HTTPException(409,'render_artifact_unavailable')
                sha=hashlib.sha256();md5=hashlib.md5();measured=0
                with path.open('rb') as f:
                    while chunk:=f.read(1024*1024):sha.update(chunk);md5.update(chunk);measured+=len(chunk)
                if measured!=size or sha.hexdigest()!=result['sha256'] or md5.hexdigest()!=result['md5']:raise HTTPException(409,'native_render_checksum_mismatch')
                p['result']=result;source['media_result']=result;source['project_archive_required']=True
                db.execute("UPDATE outbox SET status='PRIVATE_VIDEO_READY',payload=%s WHERE id=%s",(json.dumps(p),mid))
                db.execute("UPDATE outbox SET status='PRIVATE_VIDEO_READY',payload=%s WHERE id=%s",(json.dumps(source,ensure_ascii=False),parent['id']))
                db.execute("UPDATE stories SET status='PRIVATE_VIDEO_READY' WHERE id=%s",(p['story_id'],))
                qa_summary={check['check_id']:{'status':check['status'],'evidence_artifact_ids':check['evidence_artifact_ids'],'checker_version':check['checker_version']} for check in qa_checks}
                db.execute('INSERT INTO story_versions(story_id,version,payload,qa) VALUES(%s,%s,%s,%s) ON CONFLICT(story_id,version) DO NOTHING',(p['story_id'],story_version(p,media=True),json.dumps({'plan':source['plan'],'media_result':result},ensure_ascii=False),json.dumps({'checks':qa_summary,'human_review':'NOT_PERFORMED','publication_authorized':False})))
                for check in qa_checks:
                    db.execute("INSERT INTO qa_checks(story_id,job_id,check_name,status,evidence) VALUES(%s,%s,%s,%s,%s)",
                        (p['story_id'],render_id,check['check_id'],check['status'],json.dumps({'evidence_artifact_ids':check['evidence_artifact_ids'],'checker_version':check['checker_version']})))
                db.execute("INSERT INTO outbox(event_type,aggregate_id,payload,dedup_key,status) VALUES('automatic_video_archive',%s,%s,%s,'PENDING') ON CONFLICT(dedup_key) DO NOTHING",(p['story_id'],json.dumps({'media_intent_id':str(mid),'task_id':str(parent['id']),'story_id':p['story_id'],'source_key':source['source_key'],'source_date':source['source_date'],'title':source['plan']['title'],'render_job_id':str(render_id),'sha256':sha.hexdigest(),'md5':md5.hexdigest(),'size_bytes':size,'duration_seconds':duration,'qa_checks':qa_summary,'rights_state':source.get('rights_state','UNVERIFIED'),'rights_evidence':source.get('rights_evidence',{}),'production_authorized':p.get('production_authorized') is True,'production_approval':p.get('production_approval',{}),'publication_authorized':False,'project_archive_required':True},ensure_ascii=False),'automatic-video-archive:'+p['story_id']))
            return {'state':'PRIVATE_VIDEO_READY','media_intent_id':str(mid),'youtube_upload':False,'result':result}
        except HTTPException:raise
        except Exception:raise HTTPException(503,'media_completion_unavailable') from None

    @router.post('/media-tasks/{mid}/fail')
    async def fail(mid:int,request:Request,authorization:str|None=Header(default=None)):
        auth(authorization);b=await body(request,{'media_claim_id','code'})
        if b['code'] not in ('media_outcome_uncertain','visual_review_rejected','media_runtime_failed'):raise HTTPException(422,'invalid_failure_code')
        try:
            with connection_factory() as db:
                row,p,_,source=task(db,mid)
                if row['status']!='MEDIA_RUNNING' or p.get('media_claim_id')!=b['media_claim_id']:raise HTTPException(409,'media_claim_conflict')
                p['error_code']=b['code'];db.execute("UPDATE outbox SET status='NEEDS_REVIEW',payload=%s WHERE id=%s",(json.dumps(p),mid));db.execute("UPDATE stories SET status='MEDIA_NEEDS_REVIEW' WHERE id=%s",(p['story_id'],))
            return {'state':'NEEDS_REVIEW','youtube_upload':False}
        except HTTPException:raise
        except Exception:raise HTTPException(503,'media_failure_unavailable') from None
    return router
