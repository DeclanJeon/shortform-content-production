"""Persist a source/plan-bound AI originality assessment and expose safe approvals."""
import copy
import hmac
import json
from fastapi import APIRouter, Header, HTTPException, Request
from creative.contract import canonical_sha256
from creative.publication_rights import assess, effective_rights, valid_policy, qa08, POLICY


FIELDS={'plan_sha256','source_document_sha256','source_input_sha256','review_request_sha256','checker_version','review'}


def create_production_policy_router(connection,token,auto_enabled=lambda:False):
    router=APIRouter(prefix='/v1/automation')
    def auth(value):
        try:ok=isinstance(value,str) and value.startswith('Bearer ') and hmac.compare_digest(value[7:].encode('ascii'),token.encode('ascii'))
        except UnicodeEncodeError:ok=False
        if not ok:raise HTTPException(401,'unauthorized')

    @router.get('/policy-tasks')
    def tasks(authorization:str|None=Header(default=None)):
        auth(authorization)
        with connection() as db:
            rows=db.execute("""SELECT id,payload FROM outbox WHERE event_type='creative_production'
                AND status IN ('PLAN_READY','PRIVATE_VIDEO_READY') AND NOT (payload ? 'content_policy')
                ORDER BY id LIMIT 12""").fetchall()
        return {'tasks':[{'task_id':str(r['id']),**{k:r['payload'][k] for k in ('story_id','source','source_sha256','plan','plan_sha256')},'plan_version':r['payload'].get('plan_version',1)} for r in rows]}

    @router.post('/tasks/{task_id}/policy')
    async def submit(task_id:int,request:Request,authorization:str|None=Header(default=None)):
        auth(authorization);raw=bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw)>64*1024:raise HTTPException(413,'policy_request_too_large')
        try:body=json.loads(raw)
        except (ValueError,UnicodeDecodeError):raise HTTPException(422,'invalid_policy_body') from None
        if not isinstance(body,dict) or set(body)!=FIELDS:raise HTTPException(422,'invalid_policy_body')
        with connection() as db:
            # Same lock order as media completion: child then parent.
            child=db.execute("SELECT id,status,payload FROM outbox WHERE event_type='creative_plan_ready' AND payload->>'task_id'=%s AND payload->>'plan_sha256'=%s AND status<>'SUPERSEDED' FOR UPDATE",(str(task_id),body['plan_sha256'])).fetchone()
            row=db.execute("SELECT id,status,payload FROM outbox WHERE id=%s AND event_type='creative_production' FOR UPDATE",(task_id,)).fetchone()
            if not child or not row:raise HTTPException(404,'policy_plan_not_found')
            p=row['payload'];cp=child['payload']
            if (p.get('plan_sha256')!=body['plan_sha256'] or cp.get('plan_sha256')!=body['plan_sha256']
                    or p.get('source_sha256')!=body['source_document_sha256']
                    or canonical_sha256(p['source'])!=body['source_input_sha256']):
                raise HTTPException(409,'policy_lineage_conflict')
            try:policy=assess(p['source'],p['plan'],p['source_sha256'],body['review'],body['review_request_sha256'],body['checker_version'])
            except (ValueError,TypeError,KeyError):raise HTTPException(422,'originality_review_invalid') from None
            if p.get('content_policy'):
                if p['content_policy']!=policy:raise HTTPException(409,'policy_already_frozen')
                return {'state':policy['decision'],'evidence_artifact_id':policy['evidence_artifact_id'],'policy_version':POLICY}
            p['content_policy']=policy
            cp['content_policy_evidence']=policy['evidence_artifact_id']
            # A later independent assessment may resolve QA-08 without rerendering.
            if p.get('media_result'):
                result=copy.deepcopy(p['media_result'])
                result['qa_checks']=[qa08(p) if q['check_id']=='QA-08' else q for q in result['qa_checks']]
                p['media_result']=result;cp['result']=result
                check=next(q for q in result['qa_checks'] if q['check_id']=='QA-08')
                db.execute("UPDATE qa_checks SET status=%s,evidence=%s,checked_at=now() WHERE story_id=%s AND job_id=%s AND check_name='QA-08'",(check['status'],json.dumps({k:check[k] for k in ('evidence_artifact_ids','checker_version')}),p['story_id'],result['render_job_id']))
                archive=db.execute("SELECT id,payload FROM outbox WHERE event_type='automatic_video_archive' AND aggregate_id=%s FOR UPDATE",(p['story_id'],)).fetchone()
                if archive:
                    a=archive['payload'];state,evidence=effective_rights(p)
                    a['rights_state']=state;a['rights_evidence']=evidence
                    a['qa_checks']['QA-08']={k:check[k] for k in ('status','evidence_artifact_ids','checker_version')}
                    db.execute('UPDATE outbox SET payload=%s WHERE id=%s',(json.dumps(a),archive['id']))
            db.execute('UPDATE outbox SET payload=%s WHERE id=%s',(json.dumps(p,ensure_ascii=False),task_id))
            db.execute('UPDATE outbox SET payload=%s WHERE id=%s',(json.dumps(cp,ensure_ascii=False),child['id']))
            db.execute("INSERT INTO outbox(event_type,aggregate_id,payload,dedup_key,status) VALUES('content_policy_assessed',%s,%s,%s,'RECORDED') ON CONFLICT(dedup_key) DO NOTHING",(p['story_id'],json.dumps(policy,ensure_ascii=False),'content-policy:'+p['story_id']+':'+p['plan_sha256']))
        return {'state':policy['decision'],'evidence_artifact_id':policy['evidence_artifact_id'],'policy_version':POLICY}

    @router.get('/next-policy-approval')
    def next_approval(authorization:str|None=Header(default=None)):
        auth(authorization)
        if not auto_enabled():return {'ready':False,'reason':'automatic_policy_approval_disabled'}
        with connection() as db:
            rows=db.execute("SELECT id,payload FROM outbox WHERE event_type='creative_plan_ready' AND status='AWAITING_PRODUCTION_APPROVAL' ORDER BY id LIMIT 12").fetchall()
            for child in rows:
                cp=child['payload']
                parent=db.execute("SELECT payload FROM outbox WHERE id=%s AND event_type='creative_production'",(int(cp['task_id']),)).fetchone()
                if not parent:continue
                p=parent['payload']
                if p.get('planning_lane')=='standby':continue
                if cp['plan_sha256']!=p.get('plan_sha256') or not valid_policy(p):continue
                if p.get('review')!={'decision':'PASS','issues':[]}:continue
                # Fixed plan bounds are also checked by validate_plan in valid_policy.
                evidence=p['content_policy']['evidence_artifact_id']
                return {'ready':True,'policy_version':POLICY,'policy_evidence':evidence,
                        'body':{'review_id':cp['review_id'],'decision':'approve',
                                'event_id':'auto-policy:'+str(child['id'])+':'+evidence.split(':')[-1][:32]}}
        return {'ready':False,'reason':'no_policy_approved_plan'}
    return router
