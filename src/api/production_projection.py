"""Leased, revision-bound Sheets projection. PostgreSQL remains authoritative."""
import hashlib
import hmac
import json
import re
import uuid
from fastapi import APIRouter,Header,HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel,ConfigDict,Field

HEADER=['story_id','task_id','source_date','source_key','title','production_state','media_state','originality_policy',
        'duration_seconds','video_archive_state','video_drive_url','project_archive_state','project_drive_url',
        'release_state','youtube_url','error_code','projection_sha256']
PROJECTION_SCHEMA_VERSION='collection-history-used-checkbox-v1'


def snapshot(db,story):
    rows=db.execute("SELECT id,event_type,status,payload FROM outbox WHERE aggregate_id=%s AND event_type IN ('creative_production','creative_plan_ready','automatic_video_archive','automatic_project_archive','publish_sheet_handoff') ORDER BY id",(story,)).fetchall()
    by={r['event_type']:r for r in rows}
    parent=by.get('creative_production')
    if not parent:return None
    p=parent['payload'];child=by.get('creative_plan_ready',{});cp=child.get('payload',{})
    video=by.get('automatic_video_archive',{});bundle=by.get('automatic_project_archive',{})
    handoff=by.get('publish_sheet_handoff',{})
    def drive(row):
        file=row.get('payload',{}).get('archive_drive_file_id','')
        return 'https://drive.google.com/file/d/'+file+'/view' if row.get('status')=='ARCHIVED' and re.fullmatch(r'[A-Za-z0-9_-]{10,200}',file) else ''
    error=cp.get('last_media_error',cp.get('error_code','')) if child.get('status')=='NEEDS_REVIEW' or parent['status']=='NEEDS_REVIEW' else ''
    if not isinstance(error,str) or not re.fullmatch(r'[a-z0-9_]{0,120}',error):error=''
    policy=p.get('content_policy',{});result=p.get('media_result',{})
    values=[story,str(parent['id']),p.get('source_date',''),p.get('source_key',''),p.get('plan',{}).get('title',''),
            parent['status']+(':standby' if p.get('planning_lane')=='standby' else ''),child.get('status',''),(policy.get('policy_version','')+':'+policy.get('decision','')) if policy else 'NOT_ASSESSED',
            result.get('duration_seconds',''),video.get('status',''),drive(video),bundle.get('status',''),drive(bundle),
            handoff.get('status',''),'',
            error]
    values=[str(value) for value in values]
    digest=hashlib.sha256(json.dumps(
        {'schema_version':PROJECTION_SCHEMA_VERSION,'values':values},
        ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
    values.append(digest)
    if handoff.get('status')=='DELIVERED':status='발행 대기 시트 전달 완료 · 업로드는 별도 publisher'
    elif handoff.get('status')=='IN_PROGRESS':status='발행 대기 시트 전달중'
    elif video.get('status')=='ARCHIVED' and bundle.get('status')=='ARCHIVED':status='비공개 보관완료 · 공개 대기'
    elif child.get('status')=='PRIVATE_VIDEO_READY':status='영상 제작완료 · 비공개 보관 대기'
    elif child.get('status')=='NEEDS_REVIEW' or parent['status']=='NEEDS_REVIEW':status='제작 검토필요'+(' · '+error if error else '')
    elif child.get('status')=='MEDIA_RUNNING':status='영상 제작중'
    elif p.get('planning_lane')=='standby':status='예비 기획 · 검수 완료' if parent['status']=='PLAN_READY' else '예비 기획 · 준비중'
    elif cp.get('production_authorized') is True:status='제작 승인 · 대기'
    elif parent['status']=='PLAN_READY':status='기획완료 · 제작 승인 대기'
    elif parent['status']=='CREATIVE_RUNNING':status='기획 생성중'
    else:status='소재 선정 · 기획 대기'
    if not values[2] or not values[3]:return None
    return {'story_id':story,'source_date':values[2],'source_key':values[3],'header':HEADER,'values':values,'snapshot_sha256':digest,'source_status':status}


class ProjectionAck(BaseModel):
    model_config=ConfigDict(extra='forbid')
    claim_id:uuid.UUID
    snapshot_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
    ledger_row:int=Field(ge=2,le=20000,strict=True)
    source_row:int=Field(ge=2,le=20000,strict=True)
    sheets_readback_verified:bool


def create_production_projection_router(connection,token):
    router=APIRouter(prefix='/v1/production-projections')
    def auth(value):
        try:ok=isinstance(value,str) and value.startswith('Bearer ') and hmac.compare_digest(value[7:].encode('ascii'),token.encode('ascii'))
        except UnicodeEncodeError:ok=False
        if not ok:raise HTTPException(401,'unauthorized')

    @router.post('/claim')
    def claim(authorization:str|None=Header(default=None)):
        auth(authorization)
        with connection() as db:
            db.execute("SELECT pg_advisory_xact_lock(hashtext('shortform-sheet-projection'))")
            busy=db.execute("SELECT id FROM outbox WHERE event_type='production_projection' AND status='IN_PROGRESS' AND next_attempt_at>now() LIMIT 1").fetchone()
            if busy:return {'ready':False}
            rows=db.execute("SELECT aggregate_id FROM outbox WHERE event_type='creative_production' AND created_at>now()-interval '90 days' ORDER BY id LIMIT 1000").fetchall()
            for r in rows:
                data=snapshot(db,r['aggregate_id'])
                if not data:continue
                old=db.execute("SELECT id,status,payload,next_attempt_at>now() AS waiting FROM outbox WHERE event_type='production_projection' AND aggregate_id=%s FOR UPDATE",(r['aggregate_id'],)).fetchone()
                if old and old['status']=='DELIVERED' and old['waiting'] and old['payload']['snapshot_sha256']==data['snapshot_sha256']:continue
                data['claim_id']=str(uuid.uuid4())
                if old:
                    db.execute("UPDATE outbox SET status='IN_PROGRESS',payload=%s,attempts=attempts+1,next_attempt_at=now()+interval '15 minutes' WHERE id=%s",(json.dumps(data,ensure_ascii=False),old['id']));pid=old['id']
                else:
                    new=db.execute("INSERT INTO outbox(event_type,aggregate_id,status,payload,dedup_key,next_attempt_at) VALUES('production_projection',%s,'IN_PROGRESS',%s,%s,now()+interval '15 minutes') RETURNING id",(r['aggregate_id'],json.dumps(data,ensure_ascii=False),'production-projection:'+r['aggregate_id'])).fetchone();pid=new['id']
                return {'ready':True,'projection_id':str(pid),**data}
        return {'ready':False}

    @router.post('/{projection_id}/ack')
    def ack(projection_id:int,body:ProjectionAck,authorization:str|None=Header(default=None)):
        auth(authorization)
        if body.sheets_readback_verified is not True:raise HTTPException(409,'projection_readback_required')
        with connection() as db:
            row=db.execute("SELECT id,status,payload FROM outbox WHERE id=%s AND event_type='production_projection' FOR UPDATE",(projection_id,)).fetchone()
            if not row:raise HTTPException(404,'projection_not_found')
            data=row['payload']
            if data.get('claim_id')!=str(body.claim_id) or data.get('snapshot_sha256')!=body.snapshot_sha256:raise HTTPException(409,'projection_claim_conflict')
            if row['status']=='DELIVERED':return {'state':'DELIVERED','projection_id':str(projection_id)}
            if row['status']!='IN_PROGRESS':raise HTTPException(409,'projection_claim_conflict')
            current=snapshot(db,row.get('aggregate_id') or data['story_id'])
            if not current or current['snapshot_sha256']!=body.snapshot_sha256:
                db.execute("UPDATE outbox SET status='PENDING',next_attempt_at=now() WHERE id=%s",(projection_id,))
                return JSONResponse(status_code=409,content={'state':'STALE_SNAPSHOT'})
            data['readback']={'ledger_row':body.ledger_row,'source_row':body.source_row,'sheets_readback_verified':True}
            db.execute("UPDATE outbox SET status='DELIVERED',payload=%s,next_attempt_at=now()+interval '6 hours' WHERE id=%s",(json.dumps(data,ensure_ascii=False),projection_id))
        return {'state':'DELIVERED','projection_id':str(projection_id)}
    return router
