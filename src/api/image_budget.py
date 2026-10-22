"""Durable image-call admission. Reservations are policy estimates, not invoices."""
import hashlib
import hmac
import json
import re
import uuid
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field


class ImageIntent(BaseModel):
    model_config = ConfigDict(extra='forbid')
    input_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    revision: int = Field(default=1,ge=1,le=6,strict=True)
    repair_evidence_sha256: str | None = Field(default=None,pattern=r'^[0-9a-f]{64}$')


class ImageReceipt(ImageIntent):
    claim_id: uuid.UUID
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')


def create_image_budget_router(connection, token, enabled, policy):
    router = APIRouter(prefix='/v1/automation/image-intents')
    def auth(value):
        try:
            ok=isinstance(value,str) and value.startswith('Bearer ') and hmac.compare_digest(value[7:].encode('ascii'),token.encode('ascii'))
        except UnicodeEncodeError:ok=False
        if not ok:raise HTTPException(401,'unauthorized')
    def identity(story_id, asset_id):
        if not re.fullmatch(r'[0-9]{8}-auto-[0-9a-f]{12}',story_id) or not re.fullmatch(r'(?:CH|SH)[0-9]{2}',asset_id):
            raise HTTPException(422,'invalid_image_identity')
        return f'image-intent:{story_id}:{asset_id}'
    def result(row,p):
        return {'intent_id':str(row['id']),'state':row['status'],'claim_id':p['claim_id'],
                'input_sha256':p['input_sha256'],'reservation_usd':p['reservation_usd'],
                'cost_kind':'policy_estimate_not_invoice','sha256':p.get('sha256'),
                'revision':p.get('revision',1),'repair_evidence_sha256':p.get('repair_evidence_sha256')}

    @router.post('/{story_id}/{asset_id}/reserve')
    def reserve(story_id:str,asset_id:str,body:ImageIntent,authorization:str|None=Header(default=None)):
        auth(authorization);base_key=identity(story_id,asset_id)
        key=base_key if body.revision==1 else base_key+f':v{body.revision}'
        if body.revision>=2 and not body.repair_evidence_sha256:raise HTTPException(422,'repair_evidence_required')
        if not enabled():raise HTTPException(409,'image_dispatch_disabled')
        cfg=policy()
        try:
            cost=Decimal(str(cfg['per_image_usd']));day_limit=Decimal(str(cfg['daily_usd']));episode_limit=Decimal(str(cfg['episode_usd']))
            calls=int(cfg['daily_calls'])
            if not all(x.is_finite() and x>0 for x in (cost,day_limit,episode_limit)) or not 1<=calls<=12*27*2:raise ValueError()
        except (KeyError,ValueError,ArithmeticError,TypeError):raise HTTPException(503,'image_budget_unconfigured') from None
        day=datetime.now(ZoneInfo('Asia/Seoul')).date().isoformat()
        with connection() as db:
            db.execute('SELECT pg_advisory_xact_lock(hashtext(%s))',('image-budget:'+day,))
            old=db.execute("SELECT id,status,payload FROM outbox WHERE event_type='image_call_intent' AND dedup_key=%s FOR UPDATE",(key,)).fetchone()
            if old:
                p=old['payload']
                if p['input_sha256']!=body.input_sha256:raise HTTPException(409,'image_input_conflict')
                # An unknown external outcome is never a fresh paid-call permit.
                if old['status']!='SUCCEEDED':raise HTTPException(409,'image_outcome_uncertain')
                return result(old,p)
            if body.revision>=2:
                prior_key=base_key if body.revision==2 else base_key+f':v{body.revision-1}'
                prior=db.execute("SELECT id,status,payload FROM outbox WHERE event_type='image_call_intent' AND dedup_key=%s FOR UPDATE",(prior_key,)).fetchone()
                if not prior or prior['status']!='SUCCEEDED':raise HTTPException(409,'known_previous_image_required')
            row=db.execute("SELECT payload FROM outbox WHERE event_type='creative_plan_ready' AND aggregate_id=%s AND status='MEDIA_RUNNING' FOR UPDATE",(story_id,)).fetchone()
            if not row:raise HTTPException(409,'media_claim_required')
            p=row['payload'];approval=p.get('production_approval',{})
            if p.get('production_authorized') is not True or approval.get('plan_sha256')!=p.get('plan_sha256'):
                raise HTTPException(409,'production_approval_required')
            parent=db.execute("SELECT payload FROM outbox WHERE id=%s AND event_type='creative_production'",(int(p['task_id']),)).fetchone()
            if not parent or parent['payload'].get('plan_sha256')!=p['plan_sha256']:raise HTTPException(409,'image_plan_lineage_invalid')
            plan=parent['payload']['plan']
            if asset_id not in {x['id'] for x in [*plan['characters'],*plan['shots']]}:raise HTTPException(422,'image_asset_not_in_plan')
            totals=db.execute("""SELECT count(*) AS calls,COALESCE(sum((payload->>'reservation_usd')::numeric),0) AS cost
                FROM outbox WHERE event_type='image_call_intent' AND payload->>'usage_date'=%s""",(day,)).fetchone()
            episode=db.execute("SELECT COALESCE(sum((payload->>'reservation_usd')::numeric),0) AS cost FROM outbox WHERE event_type='image_call_intent' AND aggregate_id=%s",(story_id,)).fetchone()
            if totals['calls']>=calls or Decimal(totals['cost'])+cost>day_limit or Decimal(episode['cost'])+cost>episode_limit:
                raise HTTPException(409,'image_budget_exhausted')
            intent={'story_id':story_id,'asset_id':asset_id,'plan_sha256':p['plan_sha256'],
                    'input_sha256':body.input_sha256,'claim_id':str(uuid.uuid4()),'usage_date':day,
                    'reservation_usd':str(cost),'provider':'codex-imagen','cost_kind':'policy_estimate_not_invoice',
                    'revision':body.revision,'repair_evidence_sha256':body.repair_evidence_sha256}
            row=db.execute("INSERT INTO outbox(event_type,aggregate_id,payload,dedup_key,status) VALUES('image_call_intent',%s,%s,%s,'IN_PROGRESS') RETURNING id,status",(story_id,json.dumps(intent),key)).fetchone()
        return result(row,intent)

    @router.post('/{story_id}/{asset_id}/complete')
    def complete(story_id:str,asset_id:str,body:ImageReceipt,authorization:str|None=Header(default=None)):
        auth(authorization);key=identity(story_id,asset_id)
        if body.revision>=2:key+=f':v{body.revision}'
        with connection() as db:
            row=db.execute("SELECT id,status,payload FROM outbox WHERE event_type='image_call_intent' AND dedup_key=%s FOR UPDATE",(key,)).fetchone()
            if not row:raise HTTPException(404,'image_intent_not_found')
            p=row['payload']
            if p['input_sha256']!=body.input_sha256 or p['claim_id']!=str(body.claim_id):raise HTTPException(409,'image_receipt_conflict')
            if row['status']=='SUCCEEDED':
                if p.get('sha256')!=body.sha256:raise HTTPException(409,'image_receipt_conflict')
                return result(row,p)
            if row['status']!='IN_PROGRESS':raise HTTPException(409,'image_intent_not_running')
            p['sha256']=body.sha256
            db.execute("UPDATE outbox SET status='SUCCEEDED',payload=%s WHERE id=%s",(json.dumps(p),row['id']))
        return result({**row,'status':'SUCCEEDED'},p)
    return router
