"""Operator-authorized revision of a held, unstarted plan. Never reuse an approval."""
from __future__ import annotations

import json
import re
from typing import Protocol
from .contract import canonical_sha256
from .pitches import select_pitch
from .plan_revision import plan_version
from .planning_contract import Json, JsonObject, PlanningFailure


class RevisionResult(Protocol):
    def fetchone(self) -> JsonObject | None: ...


class RevisionDatabase(Protocol):
    def execute(self, query: str, params: tuple[Json, ...]) -> RevisionResult: ...


def reopen_unstarted(db: RevisionDatabase, *, task_id: int, media_id: int, expected_sha256: str,
                     skill_revision: str, pitch_response: JsonObject, selected_id: str) -> JsonObject:
    """Within caller's transaction: archive lineage, supersede handoff, reopen READY.

    Caller must hold the single coordinator idle and have explicit user authority
    for this exact narration-only change. No model/media/provider request occurs.
    """
    if not re.fullmatch(r'[0-9a-f]{64}',skill_revision):
        raise PlanningFailure('planning_recovery_conflict','skill revision')
    selection=select_pitch(pitch_response)
    if not any(p['id']==selected_id for p in selection['candidates']):
        raise PlanningFailure('planning_recovery_conflict','selected pitch missing')
    parent=db.execute("SELECT id,aggregate_id,status,payload FROM outbox WHERE id=%s AND event_type='creative_production' FOR UPDATE",(task_id,)).fetchone()
    child=db.execute("SELECT id,aggregate_id,status,payload FROM outbox WHERE id=%s AND event_type='creative_plan_ready' FOR UPDATE",(media_id,)).fetchone()
    if not parent or not child:raise PlanningFailure('planning_recovery_conflict','revision task missing')
    p=parent['payload'];c=child['payload']
    prior=canonical_sha256(p.get('plan',{}))
    if (parent['status']!='PLAN_READY' or child['status']!='NEEDS_REVIEW'
            or c.get('error_code')!='narrative_revision_required'
            or c.get('task_id')!=str(task_id) or child['aggregate_id']!=parent['aggregate_id']
            or p.get('plan_sha256')!=expected_sha256 or c.get('plan_sha256')!=expected_sha256
            or prior!=expected_sha256 or (p.get('provenance') or {}).get('selected_pitch_id')!=selected_id
            or c.get('production_authorized') is not True
            or (c.get('production_approval') or {}).get('decision')!='approve'
            or (c.get('production_approval') or {}).get('plan_sha256')!=prior):
        raise PlanningFailure('planning_recovery_conflict','exact held plan required')
    if any(key in c for key in ('media_claim_id','media_claimed_at','media_claim_date','result')):
        raise PlanningFailure('planning_recovery_conflict','previous media admission')
    jobs=db.execute('SELECT count(*) AS n FROM generation_jobs WHERE story_id=%s',(parent['aggregate_id'],)).fetchone()['n']
    intents=db.execute("SELECT count(*) AS n FROM outbox WHERE aggregate_id=%s AND event_type IN ('image_call_intent','manual_youtube_release','automatic_video_archive')",(parent['aggregate_id'],)).fetchone()['n']
    if jobs or intents:raise PlanningFailure('planning_recovery_conflict','paid or uncertain media exists')
    version=plan_version(p)+1
    if version>20:raise PlanningFailure('planning_recovery_conflict','revision limit')
    revision={'kind':'unstarted_narration_revision','prior_plan_sha256':prior,'prior_plan':p['plan'],
        'selected_id':selected_id,'pitch_response':pitch_response,'previous_media_intent_id':str(media_id),
        'user_authority':'explicit_conversational_narration_correction_20261021'}
    event={'task_id':str(task_id),'story_id':p['story_id'],'plan_version':version,
        'prior_plan_sha256':prior,'prior_media_intent_id':str(media_id),'prior_parent':p,'prior_child':c,
        'new_skill_revision':skill_revision,'authority':revision['user_authority']}
    db.execute("INSERT INTO outbox(event_type,aggregate_id,payload,dedup_key,status) VALUES('narrative_revision_prepared',%s,%s,%s,'RECORDED')",
        (p['story_id'],json.dumps(event,ensure_ascii=False),f'narrative-revision-prepared:{task_id}:v{version}'))
    revised={k:v for k,v in p.items() if k not in ('plan','plan_sha256','review','provenance','content_policy',
        'claim_id','claimed_at','plan_completed_at','planning_recovery','error_code')}
    revised.update(plan_version=version,planning_revision=revision,skill_revision=skill_revision,
        selection_policy={**p.get('selection_policy',{}),'narrative_first':True,'pitches_required':True,'locked_pitch_id':selected_id})
    db.execute("UPDATE outbox SET status='SUPERSEDED',payload=payload||%s::jsonb WHERE id=%s",
        (json.dumps({'superseded_reason':'authorized_narration_revision','replacement_plan_version':version}),media_id))
    db.execute("UPDATE outbox SET status='READY',payload=%s WHERE id=%s",(json.dumps(revised,ensure_ascii=False),task_id))
    db.execute("UPDATE stories SET status='CREATIVE_READY' WHERE id=%s",(p['story_id'],))
    return {'task_id':str(task_id),'story_id':p['story_id'],'plan_version':version,'state':'READY',
        'prior_media_intent_id':str(media_id),'old_approval_preserved':True,'new_approval_required':True}
