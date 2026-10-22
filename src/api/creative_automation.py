"""Durable skill-based creative intake; model/provider work stays outside the API.

Uses existing outbox/story tables. Never grants publication authority or queues an
unsupported media adapter. Source documents are exported with native n8n OAuth.
"""
from __future__ import annotations
import hashlib
import hmac
import json
import re
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from urllib.parse import urlsplit
from fastapi import APIRouter, Header, HTTPException, Request
from sheet_intake import evaluate_snapshot, DASHBOARD_HEADERS, SHEET_ID
from creative.contract import validate_plan, canonical_sha256
from creative.topic_selection import rank_candidates
from creative.source_material import sheet_inspiration_document, EXTERNAL_INSPIRATION
from creative.planning_contract import POLICY as PLANNING_POLICY, MAX_CALLS as PLANNING_MAX_CALLS
from creative.plan_revision import plan_version, handoff_key, story_version

EVENT='creative_production'
SHA=re.compile(r'[0-9a-f]{64}\Z')
DOC=re.compile(r'/document/d/([A-Za-z0-9_-]{10,200})(?:/|$)')
CODES={'model_outcome_uncertain','creative_plan_invalid','creative_plan_review_failed','skill_bundle_changed','runtime_not_configured','local_runtime_failed','source_export_failed','planning_candidates_exhausted','planning_recovery_conflict','planning_review_invalid'}


def document_id(url):
    p=urlsplit(url)
    if p.scheme!='https' or p.hostname!='docs.google.com' or p.username or p.password or p.port not in (None,443):
        raise ValueError('invalid_creative_document_url')
    m=DOC.match(p.path)
    if not m:raise ValueError('invalid_creative_document_url')
    return m[1]


def create_creative_router(connection_factory,api_token,clock,enabled,daily_limit,skill_revision,buffer_limit=lambda:0,allow_sheet_inspiration=lambda:False,*,narrative_first=lambda:True):
    router=APIRouter(prefix='/v1/automation')
    def auth(value):
        try:valid=isinstance(value,str) and value.startswith('Bearer ') and hmac.compare_digest(value[7:].encode('ascii'),api_token.encode('ascii'))
        except UnicodeEncodeError:valid=False
        if not valid:raise HTTPException(401,'unauthorized')
    def config():
        if not enabled():raise HTTPException(409,'creative_automation_disabled')
        limit=daily_limit();revision=skill_revision()
        if isinstance(limit,bool) or not isinstance(limit,int) or not 1<=limit<=12 or not isinstance(revision,str) or not SHA.fullmatch(revision):raise HTTPException(503,'creative_automation_unconfigured')
        return limit,revision
    async def body(request,keys,max_bytes=512*1024):
        raw=bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw)>max_bytes:raise HTTPException(413,'request_too_large')
        try:b=json.loads(raw)
        except Exception:raise HTTPException(422,'invalid_body') from None
        if not isinstance(b,dict) or set(b)!=set(keys):raise HTTPException(422,'invalid_body')
        return b
    def row_payload(row):return row['payload'] if isinstance(row['payload'],dict) else json.loads(row['payload'])
    def lock_task(db,task_id):
        r=db.execute('SELECT id,status,payload FROM outbox WHERE id=%s AND event_type=%s FOR UPDATE',(task_id,EVENT)).fetchone()
        if not r:raise HTTPException(404,'creative_task_not_found')
        return r,row_payload(r)
    def save(db,task_id,state,payload):
        db.execute('UPDATE outbox SET status=%s,payload=%s WHERE id=%s',(state,json.dumps(payload,ensure_ascii=False),task_id))
    def guard_claim(r,p,b):
        if r['status']!='PLANNING' or not isinstance(b['claim_id'],str) or not hmac.compare_digest(p.get('claim_id',''),b['claim_id']):raise HTTPException(409,'creative_claim_conflict')
    def envelope(r,p,full=False):
        result={'task_id':str(r['id']),'state':r['status'],'story_id':p['story_id'],'source_key':p['source_key'],'document_id':p['document_id'],'youtube_upload':False}
        if full:result.update(claim_id=p.get('claim_id'),run_id=p['run_id'],source_date=p['source_date'],source=p['source'],source_sha256=p.get('source_sha256'),skill_revision=p['skill_revision'],plan=p.get('plan'),plan_sha256=p.get('plan_sha256'),selection_policy=p.get('selection_policy'),planning_policy=p.get('planning_policy'),planning_recovery=p.get('planning_recovery'),planning_lane=p.get('planning_lane','primary'),plan_version=plan_version(p),planning_revision=p.get('planning_revision'))
        return result

    @router.get('/status')
    def status(authorization:str|None=Header(default=None)):
        auth(authorization)
        return {'creative_enabled':bool(enabled()),'daily_story_limit':daily_limit(),'skill_revision':skill_revision(),'allow_sheet_inspiration':bool(allow_sheet_inspiration()),'automatic_publication_enabled':False}

    @router.post('/prepare')
    async def prepare(request:Request,authorization:str|None=Header(default=None)):
        auth(authorization);limit,revision=config()
        b=await body(request,{'sheet_id','dashboard_rows','summary_rows'},2*1024*1024)
        if b['sheet_id']!=SHEET_ID:raise HTTPException(422,'invalid_sheet_id')
        try:snapshot=evaluate_snapshot(b['dashboard_rows'],b['summary_rows'],clock())
        except ValueError as exc:raise HTTPException(422,str(exc)) from None
        if snapshot['status']!='READY':return {'status':snapshot['status'],'prepared_count':0,'youtube_upload':False}
        # The original accepted snapshot is immutable. Bind a separate full creative
        # input revision here; legacy intake snapshots intentionally lack these cells.
        try:
            with connection_factory() as db:
                db.execute('SELECT pg_advisory_xact_lock(hashtext(%s))',('creative-planning-buffer',))
                db.execute('SELECT pg_advisory_xact_lock(hashtext(%s))',('creative-prepare:'+snapshot['date'],))
                target=buffer_limit()
                if type(target) is not int or not 0<=target<=2:raise HTTPException(503,'planning_buffer_unconfigured')
                standby=db.execute("SELECT count(*) AS n FROM outbox WHERE event_type=%s AND payload->>'planning_lane'='standby' AND status IN ('AWAITING_SOURCE','READY','PLANNING','PLAN_READY')",(EVENT,)).fetchone()['n']
                intake=db.execute("SELECT id,payload FROM outbox WHERE dedup_key=%s AND event_type='sheet_snapshot_ready' FOR UPDATE",(f"sheet-intake:{SHEET_ID}:{snapshot['date']}",)).fetchone()
                if not intake or row_payload(intake).get('snapshot_sha256')!=snapshot['snapshot_sha256']:raise HTTPException(409,'accepted_snapshot_required')
                used=db.execute("SELECT count(*) AS n FROM outbox WHERE event_type=%s AND payload->>'source_date'=%s AND COALESCE(payload->>'planning_lane','primary')='primary'",(EVENT,snapshot['date'])).fetchone()['n']
                prepared=[]
                rows={}
                for row in b['dashboard_rows'][1:]:
                    if isinstance(row,list) and row and str(row[0])==snapshot['date']:
                        padded=[str(x) if x is not None else '' for x in row]+['']*(len(DASHBOARD_HEADERS)-len(row));rows[padded[14]]=padded
                previous=db.execute("SELECT s.title,s.source_url FROM sources s WHERE EXISTS(SELECT 1 FROM stories st WHERE st.source_id=s.id)").fetchall()
                selection=rank_candidates(snapshot,rows,previous,allow_sheet_inspiration=allow_sheet_inspiration())
                if used>=limit:selection['selected_source_key']=None;selection['dispatch_held_reason']='daily_story_limit'
                selection_key='topic-selection:'+snapshot['date']+':'+canonical_sha256(selection)
                selection_row=db.execute("INSERT INTO outbox(event_type,aggregate_id,payload,dedup_key,status) VALUES('topic_selection',%s,%s,%s,'RECORDED') ON CONFLICT(dedup_key) DO UPDATE SET dedup_key=EXCLUDED.dedup_key RETURNING id",(snapshot['date'],json.dumps(selection,ensure_ascii=False),selection_key)).fetchone()
                ranked={x['source_key']:x for x in selection['candidates']}
                items={x['source_key']:x for x in snapshot['items']}
                for candidate in selection['candidates']:
                    item=items[candidate['source_key']]
                    cells=rows[item['source_key']];seed={'title':item['title'],'category':item['category'],'source_url':item['url'],'logline':cells[16],'draft_script':cells[18],'character_notes':cells[19],'source_summary':cells[13],'creative_document_url':cells[20]}
                    inspiration=None
                    try:doc=document_id(cells[20])
                    except (ValueError,TypeError):
                        inspiration=sheet_inspiration_document(seed) if allow_sheet_inspiration() else None
                        if inspiration is None:continue
                        doc=None
                    seed_hash=canonical_sha256(seed);key='creative-production:'+hashlib.sha256(item['source_key'].encode()).hexdigest()
                    old=db.execute('SELECT id,status,payload FROM outbox WHERE dedup_key=%s',(key,)).fetchone()
                    if old:
                        prior=row_payload(old)
                        if prior['source_date']==snapshot['date'] and prior['seed_sha256']!=seed_hash:raise HTTPException(409,'creative_source_conflict')
                        continue
                    if not candidate['eligible']:continue
                    lane='primary' if used<limit else 'standby'
                    if lane=='standby' and standby>=target:continue
                    exists=db.execute("SELECT id FROM stories WHERE source_id=%s AND status IN ('PUBLISHED','MANUAL_VIDEO_COMPLETE_NOT_PUBLISHED') LIMIT 1",(item['source_key'],)).fetchone()
                    if exists:continue
                    run=db.execute("INSERT INTO runs(run_date,status,target_count) VALUES(%s,'CREATIVE_AUTOMATION',%s) ON CONFLICT(run_date) DO UPDATE SET run_date=EXCLUDED.run_date RETURNING id",(snapshot['date'],limit)).fetchone()
                    sid=snapshot['date'].replace('-','')+'-auto-'+hashlib.sha256(item['source_key'].encode()).hexdigest()[:12]
                    db.execute("INSERT INTO sources(id,run_id,source_url,platform,title,collection_method,evidence) VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(id) DO NOTHING",(item['source_key'],run['id'],item['url'],item['source_key'].split(':')[0],item['title'],'accepted_sheet_inspiration' if inspiration else 'accepted_sheet_creative_document',json.dumps({'intake_id':str(intake['id']),'source_date':snapshot['date']})))
                    db.execute("INSERT INTO stories(id,source_id,status) VALUES(%s,%s,'AWAITING_CREATIVE_SOURCE') ON CONFLICT(id) DO NOTHING",(sid,item['source_key']))
                    payload={'planning_lane':lane,'selection_policy':{'version':selection['policy_version'],'evidence_id':str(selection_row['id']),'score':candidate['score'],'reasons':candidate['reasons'],'pitches_required':True,'narrative_first':bool(narrative_first())},'schema_version':'1.0','intake_id':str(intake['id']),'run_id':str(run['id']),'story_id':sid,'source_key':item['source_key'],'source_date':snapshot['date'],'document_id':doc,'seed_sha256':seed_hash,'skill_revision':revision,'source':seed,'publication_authorized':False,'human_review':'NOT_PERFORMED'}
                    state='AWAITING_SOURCE'
                    if inspiration:
                        payload['source']={**seed,'source_material_kind':EXTERNAL_INSPIRATION,'document_text':inspiration}
                        payload['source_sha256']=hashlib.sha256(inspiration.encode('utf-8')).hexdigest();state='READY'
                        db.execute("UPDATE stories SET status='CREATIVE_READY' WHERE id=%s",(sid,))
                    r=db.execute("INSERT INTO outbox(event_type,aggregate_id,payload,dedup_key,status) VALUES(%s,%s,%s,%s,%s) RETURNING id,status",(EVENT,sid,json.dumps(payload,ensure_ascii=False),key,state)).fetchone()
                    prepared.append(str(r['id']))
                    if lane=='primary':used+=1
                    else:standby+=1
            return {'status':'PREPARED' if prepared else 'NO_NEW_CREATIVE_TASKS','prepared_count':len(prepared),'task_ids':prepared,'daily_story_limit':limit,'youtube_upload':False}
        except HTTPException:raise
        except Exception:raise HTTPException(503,'creative_intake_unavailable') from None

    @router.get('/source-tasks')
    def sources(authorization:str|None=Header(default=None)):
        auth(authorization);config()
        try:
            with connection_factory() as db:
                rows=db.execute("SELECT id,status,payload FROM outbox WHERE event_type=%s AND status='AWAITING_SOURCE' ORDER BY id LIMIT 12",(EVENT,)).fetchall()
            return {'tasks':[envelope(r,row_payload(r)) for r in rows]}
        except Exception:raise HTTPException(503,'creative_source_tasks_unavailable') from None

    @router.post('/tasks/{task_id}/source')
    async def attach_source(task_id:int,request:Request,authorization:str|None=Header(default=None)):
        auth(authorization);config();b=await body(request,{'document_id','text'})
        if not isinstance(b['text'],str) or not 60<=len(b['text'].strip())<=30000 or '\x00' in b['text']:raise HTTPException(422,'creative_source_text_invalid')
        text=b['text'].strip();digest=hashlib.sha256(text.encode()).hexdigest()
        try:
            with connection_factory() as db:
                r,p=lock_task(db,task_id)
                if b['document_id']!=p['document_id']:raise HTTPException(409,'creative_document_identity_mismatch')
                if r['status']!='AWAITING_SOURCE':
                    if p.get('source_sha256')==digest:return envelope(r,p)
                    raise HTTPException(409,'creative_document_changed')
                p['source'].update(document_text=text);p['source_sha256']=digest
                save(db,task_id,'READY',p)
                db.execute("UPDATE stories SET status='CREATIVE_READY' WHERE id=%s",(p['story_id'],))
            return {'task_id':str(task_id),'state':'READY','source_sha256':digest,'youtube_upload':False}
        except HTTPException:raise
        except Exception:raise HTTPException(503,'creative_source_attach_unavailable') from None

    @router.post('/claim')
    async def claim(request:Request,authorization:str|None=Header(default=None)):
        auth(authorization);_,revision=config();await body(request,set())
        try:
            with connection_factory() as db:
                # A lost model response is never a license for a second model request.
                db.execute("UPDATE outbox SET status='NEEDS_REVIEW',payload=payload||%s::jsonb WHERE event_type=%s AND status='PLANNING' AND next_attempt_at<now()",(json.dumps({'error_code':'model_outcome_uncertain'}),EVENT))
                r=db.execute("SELECT id,status,payload FROM outbox WHERE event_type=%s AND status='READY' ORDER BY CASE WHEN payload->>'planning_lane'='standby' THEN 1 ELSE 0 END,id FOR UPDATE SKIP LOCKED LIMIT 1",(EVENT,)).fetchone()
                if not r:
                    # One fenced policy upgrade for completed legacy planning
                    # rejection only. Never reclaim uncertain/auth/paid/approved work.
                    r=db.execute("""SELECT o.id,o.status,o.payload FROM outbox o
                        WHERE o.event_type=%s AND o.status='NEEDS_REVIEW'
                        AND o.payload->>'error_code'='creative_plan_review_failed'
                        AND NOT (o.payload ? 'planning_policy') AND NOT (o.payload ? 'planning_recovery')
                        AND NOT (o.payload ? 'plan') AND o.payload->>'skill_revision'=%s
                        AND o.payload->'selection_policy'->>'pitches_required'='true'
                        AND NOT EXISTS(SELECT 1 FROM story_versions v WHERE v.story_id=o.aggregate_id)
                        AND NOT EXISTS(SELECT 1 FROM generation_jobs g WHERE g.story_id=o.aggregate_id)
                        AND NOT EXISTS(SELECT 1 FROM outbox child WHERE child.aggregate_id=o.aggregate_id
                            AND child.event_type IN ('creative_plan_ready','image_call_intent','manual_youtube_release'))
                        ORDER BY o.id FOR UPDATE OF o SKIP LOCKED LIMIT 1""",(EVENT,revision)).fetchone()
                    if not r:return {'task':None}
                    p=row_payload(r)
                    p['planning_recovery']={'policy':PLANNING_POLICY,'attempt':1,
                        'previous_claim_id':p['claim_id'],'reason':'legacy_semantic_failure'}
                    p.pop('error_code',None)
                else:p=row_payload(r)
                if p['skill_revision']!=revision:raise HTTPException(409,'skill_bundle_changed')
                p['planning_policy']=PLANNING_POLICY
                p['claim_id']=str(uuid.uuid4());p['claimed_at']=datetime.now(timezone.utc).isoformat()
                save(db,r['id'],'PLANNING',p)
                db.execute("UPDATE outbox SET attempts=attempts+1,next_attempt_at=now()+interval '90 minutes' WHERE id=%s",(r['id'],))
                r={**r,'status':'PLANNING'}
            return {'task':envelope(r,p,True)}
        except HTTPException:raise
        except Exception:raise HTTPException(503,'creative_claim_unavailable') from None

    @router.post('/promote-buffer')
    async def promote_buffer(request:Request,authorization:str|None=Header(default=None)):
        auth(authorization);config();await body(request,set())
        if buffer_limit()==0:return {'promoted':False,'reason':'buffer_disabled'}
        today=clock().astimezone(ZoneInfo('Asia/Seoul')).date().isoformat()
        from creative.publication_rights import valid_policy
        with connection_factory() as db:
            db.execute('SELECT pg_advisory_xact_lock(hashtext(%s))',('creative-planning-buffer',))
            if db.execute("SELECT 1 FROM outbox WHERE event_type='planning_buffer_promotion' AND aggregate_id=%s",(today,)).fetchone():return {'promoted':False,'reason':'already_promoted_today'}
            failed=db.execute("""SELECT id,aggregate_id,payload FROM outbox o WHERE event_type='creative_production'
                AND status='NEEDS_REVIEW' AND payload->>'error_code'='planning_candidates_exhausted'
                AND payload->>'source_date'=%s AND COALESCE(payload->>'planning_lane','primary')='primary'
                AND NOT EXISTS(SELECT 1 FROM generation_jobs g WHERE g.story_id=o.aggregate_id)
                AND NOT EXISTS(SELECT 1 FROM outbox a WHERE a.aggregate_id=o.aggregate_id AND a.event_type='image_call_intent')
                ORDER BY id LIMIT 1 FOR UPDATE""",(today,)).fetchone()
            if not failed:return {'promoted':False,'reason':'no_eligible_primary_failure'}
            active=db.execute("""SELECT 1 FROM outbox WHERE event_type='creative_production'
                AND COALESCE(payload->>'planning_lane','primary')='primary'
                AND status IN ('AWAITING_SOURCE','READY','PLANNING','PLAN_READY') LIMIT 1""").fetchone()
            used=db.execute("SELECT count(*) AS n FROM outbox WHERE event_type='creative_plan_ready' AND payload->>'media_claim_date'=%s",(today,)).fetchone()['n']
            if active or used>=daily_limit():return {'promoted':False,'reason':'primary_or_media_slot_busy'}
            choices=db.execute("SELECT id,aggregate_id,payload FROM outbox WHERE event_type=%s AND status='PLAN_READY' AND payload->>'planning_lane'='standby' ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 2",(EVENT,)).fetchall()
            for row in choices:
                p=row['payload']
                if p.get('review')!={'decision':'PASS','issues':[]} or not valid_policy(p):continue
                p.update(planning_lane='primary',buffer_promoted_for=failed['aggregate_id'],buffer_promotion_date=today)
                db.execute('UPDATE outbox SET payload=%s WHERE id=%s',(json.dumps(p,ensure_ascii=False),row['id']))
                db.execute("UPDATE outbox SET payload=payload||%s::jsonb WHERE event_type='creative_plan_ready' AND payload->>'task_id'=%s",(json.dumps({'planning_lane':'primary'}),str(row['id'])))
                event={'task_id':str(row['id']),'story_id':row['aggregate_id'],'replaces_task_id':str(failed['id']),'plan_sha256':p['plan_sha256']}
                db.execute("INSERT INTO outbox(event_type,aggregate_id,payload,dedup_key,status) VALUES('planning_buffer_promotion',%s,%s,%s,'RECORDED')",(today,json.dumps(event),'planning-buffer-promotion:'+today))
                return {'promoted':True,**event}
        return {'promoted':False,'reason':'no_reviewed_rights_cleared_buffer'}

    @router.get('/tasks/{task_id}')
    def task(task_id:int,authorization:str|None=Header(default=None)):
        auth(authorization)
        try:
            with connection_factory() as db:
                r,p=lock_task(db,task_id)
            return envelope(r,p,True)
        except HTTPException:raise
        except Exception:raise HTTPException(503,'creative_task_unavailable') from None

    @router.post('/tasks/{task_id}/complete-plan')
    async def complete(task_id:int,request:Request,authorization:str|None=Header(default=None)):
        auth(authorization);config();b=await body(request,{'claim_id','plan','review','provenance'})
        try:validate_plan(b['plan'])
        except (ValueError,TypeError,KeyError):raise HTTPException(422,'creative_plan_invalid') from None
        review=b['review'];proof=b['provenance']
        if not isinstance(review,dict) or set(review)!={'decision','issues'} or review.get('decision')!='PASS' or review.get('issues')!=[]:raise HTTPException(422,'creative_plan_review_failed')
        if not isinstance(proof,dict) or proof.get('review_kind')!='AI_CONTENT_REVIEW' or proof.get('human_review')!='NOT_PERFORMED' or type(proof.get('call_count')) is not int or not 2<=proof['call_count']<=(PLANNING_MAX_CALLS if proof.get('planning_policy')==PLANNING_POLICY else 7):raise HTTPException(422,'creative_provenance_invalid')
        hashes=proof.get('request_hashes');files=proof.get('skill_files')
        if not isinstance(hashes,list) or len(hashes)!=proof['call_count'] or any(not isinstance(x,str) or not SHA.fullmatch(x) for x in hashes) or not isinstance(files,list) or not 1<=len(files)<=40:raise HTTPException(422,'creative_provenance_invalid')
        if any(not isinstance(f,dict) or set(f)!={'path','sha256'} or not isinstance(f['path'],str) or not 1<=len(f['path'])<=500 or f['path'].startswith('/') or '..' in f['path'].split('/') or not isinstance(f['sha256'],str) or not SHA.fullmatch(f['sha256']) for f in files):raise HTTPException(422,'creative_provenance_invalid')
        if canonical_sha256(files)!=proof.get('skill_revision') or any(not isinstance(proof.get(k),str) or not 1<=len(proof[k])<=100 for k in ('provider','model')):raise HTTPException(422,'creative_provenance_invalid')
        if proof.get('planning_policy') is not None:
            receipts=proof.get('execution_receipts')
            if (proof['planning_policy']!=PLANNING_POLICY or not isinstance(receipts,list)
                    or len(receipts)!=proof['call_count'] or not SHA.fullmatch(str(proof.get('planning_context_sha256','')))
                    or proof.get('selected_pitch_id') not in ('P1','P2','P3')):raise HTTPException(422,'creative_provenance_invalid')
            for receipt,digest in zip(receipts,hashes):
                execution=receipt.get('execution') if isinstance(receipt,dict) else None
                if (not isinstance(receipt,dict) or receipt.get('prompt_sha256')!=digest
                        or not isinstance(execution,dict) or set(execution)!={'provider','model','thinking'}
                        or any(not isinstance(execution[k],str) or not 1<=len(execution[k])<=100 for k in execution)):
                    raise HTTPException(422,'creative_provenance_invalid')
        narrative_profile=('schema_version' in b['plan']
                           and (b['plan']['schema_version']=='2.0'
                                or (b['plan']['schema_version']=='3.0'
                                    and isinstance(proof.get('narrative_review'),dict))))
        if narrative_profile:
            from creative.narrative import validate_proof
            try:validate_proof(b['plan'],proof)
            except (ValueError,TypeError,KeyError):raise HTTPException(422,'creative_narrative_evidence_invalid') from None
        digest=canonical_sha256(b['plan'])
        try:
            with connection_factory() as db:
                r,p=lock_task(db,task_id)
                if proof.get('skill_revision')!=p['skill_revision']:raise HTTPException(409,'skill_bundle_changed')
                if ((p.get('selection_policy') or {}).get('narrative_first')
                        and b['plan']['schema_version'] not in ('2.0', '3.0')):
                    raise HTTPException(422,'creative_narrative_profile_required')
                child_key=handoff_key(task_id,p)
                if r['status'] in ('PLAN_READY','PRIVATE_VIDEO_READY','ARCHIVED_PRIVATE') and p.get('claim_id')==b['claim_id'] and p.get('plan_sha256')==digest:
                    child=db.execute("SELECT id,payload FROM outbox WHERE event_type='creative_plan_ready' AND dedup_key=%s",(child_key,)).fetchone()
                    if not child:raise HTTPException(409,'creative_review_target_missing')
                    child_payload=row_payload(child)
                    review_id=f"production:{child['id']}:{digest}"
                    if child_payload.get('review_id')!=review_id:raise HTTPException(409,'creative_review_target_conflict')
                    return {'task_id':str(task_id),'state':'PLAN_READY','plan_sha256':digest,'review_id':review_id,'youtube_upload':False}
                guard_claim(r,p,b)
                p.update(plan=b['plan'],review=review,provenance=proof,plan_sha256=digest,plan_completed_at=datetime.now(timezone.utc).isoformat())
                save(db,task_id,'PLAN_READY',p)
                qa={'structural_validation':'PASS','ai_content_review':'PASS','human_review':'NOT_PERFORMED','publication_authorized':False}
                db.execute('INSERT INTO story_versions(story_id,version,payload,qa) VALUES(%s,%s,%s,%s)',(p['story_id'],story_version(p),json.dumps({'plan':b['plan'],'source_sha256':p['source_sha256'],'provenance':proof},ensure_ascii=False),json.dumps(qa)))
                db.execute("UPDATE stories SET status='CREATIVE_PLAN_READY' WHERE id=%s",(p['story_id'],))
                # Production work remains held until a plan-bound signed review.
                child=db.execute("INSERT INTO outbox(event_type,aggregate_id,payload,dedup_key,status) VALUES('creative_plan_ready',%s,%s,%s,'AWAITING_PRODUCTION_APPROVAL') ON CONFLICT(dedup_key) DO NOTHING RETURNING id,status,payload",
                    (p['story_id'],json.dumps({'task_id':str(task_id),'story_id':p['story_id'],'run_id':p['run_id'],'plan_sha256':digest,'plan_version':plan_version(p),'planning_lane':p.get('planning_lane','primary'),'production_authorized':False,'publication_authorized':False}),child_key)).fetchone()
                if child is None:
                    child=db.execute("SELECT id,status,payload FROM outbox WHERE event_type='creative_plan_ready' AND dedup_key=%s FOR UPDATE",(child_key,)).fetchone()
                if child is None:raise HTTPException(503,'creative_review_target_unavailable')
                review_id=f"production:{child['id']}:{digest}"
                child_payload=row_payload(child)
                if child['status']!='AWAITING_PRODUCTION_APPROVAL' or child_payload.get('plan_sha256')!=digest or child_payload.get('story_id')!=p['story_id']:raise HTTPException(409,'creative_review_target_conflict')
                db.execute("UPDATE outbox SET payload=payload||%s::jsonb WHERE id=%s AND status='AWAITING_PRODUCTION_APPROVAL'",
                    (json.dumps({'review_id':review_id}),child['id']))
            return {'task_id':str(task_id),'state':'PLAN_READY','plan_sha256':digest,'review_id':review_id,'youtube_upload':False}
        except HTTPException:raise
        except Exception:raise HTTPException(503,'creative_completion_unavailable') from None

    @router.post('/tasks/{task_id}/fail')
    async def fail(task_id:int,request:Request,authorization:str|None=Header(default=None)):
        auth(authorization);b=await body(request,{'claim_id','code'})
        if not isinstance(b['code'],str) or b['code'] not in CODES:raise HTTPException(422,'invalid_failure_code')
        try:
            with connection_factory() as db:
                r,p=lock_task(db,task_id);guard_claim(r,p,b);p['error_code']=b['code'];save(db,task_id,'NEEDS_REVIEW',p)
                db.execute("UPDATE stories SET status='CREATIVE_NEEDS_REVIEW' WHERE id=%s",(p['story_id'],))
            return {'task_id':str(task_id),'state':'NEEDS_REVIEW','error_code':b['code'],'youtube_upload':False}
        except HTTPException:raise
        except Exception:raise HTTPException(503,'creative_failure_unavailable') from None
    return router
