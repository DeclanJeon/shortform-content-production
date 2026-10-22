"""Versioned spoken-story parsing and evidence gates; no provider or media calls."""
from __future__ import annotations

from copy import deepcopy
import re
from typing import Final, Protocol, TypedDict

from .contract import NARRATIVE_SCHEMA, canonical_sha256
from .planning_contract import JsonObject, PlanningFailure

PROFILE: Final = 'spoken-story-emotion-v2'
CRITERIA: Final = ('listener_orientation', 'causal_emotion', 'spoken_delivery', 'speech_register', 'emotional_payoff')


class PlanningCall(Protocol):
    def __call__(self, name: str, system: str, packet: JsonObject, model: str) -> JsonObject: ...


class NarrativeScript(TypedDict):
    master_text: str
    delivery: str
    emotional_arc: str


def parse_script(raw: JsonObject) -> NarrativeScript:
    """Apply the repository's dependency-free JSON contract before allocation."""
    if set(raw) != set(NARRATIVE_SCHEMA['properties']):
        raise PlanningFailure('narrative_script_invalid', 'keys')
    values: dict[str, str] = {}
    for key, schema in NARRATIVE_SCHEMA['properties'].items():
        value = raw[key]
        if not isinstance(value, str) or not schema['minLength'] <= len(value) <= schema['maxLength']:
            raise PlanningFailure('narrative_script_invalid', key)
        values[key] = value
    compact = ''.join(values['master_text'].split())
    if not 250 <= len(compact) <= 550 or sum('가' <= c <= '힣' for c in compact) < len(compact) / 2:
        raise PlanningFailure('narrative_script_invalid', 'Korean narration250..550')
    from .narration_style import register_metrics, report_register
    metrics=register_metrics(values['master_text'])
    if report_register(metrics):
        raise PlanningFailure('narrative_register_invalid',f'written-report endings {metrics}; retell causally to a listener, not by mechanically swapping endings')
    return NarrativeScript(master_text=values['master_text'], delivery=values['delivery'],
                           emotional_arc=values['emotional_arc'])


def review_verdict(raw: JsonObject) -> JsonObject:
    """Require each listener-facing check, preserving every actual rejection."""
    if set(raw) != {'decision', 'issues', 'checks'}:
        raise PlanningFailure('planning_review_invalid', 'narrative review shape')
    checks = raw['checks']
    if not isinstance(checks, list) or len(checks) != len(CRITERIA):
        raise PlanningFailure('planning_review_invalid', 'narrative check coverage')
    if {c.get('id') for c in checks if isinstance(c, dict)} != set(CRITERIA):
        raise PlanningFailure('planning_review_invalid', 'narrative check identifiers')
    for check in checks:
        if (set(check) != {'id', 'status', 'evidence'} or check['status'] not in ('PASS', 'REVISE')
                or not isinstance(check['evidence'], str) or not 10 <= len(check['evidence']) <= 600):
            raise PlanningFailure('planning_review_invalid', 'narrative check evidence')
    from .planning_runtime import _verdict
    result = _verdict({'decision':raw['decision'], 'issues':raw['issues']})
    if any(c['status'] == 'REVISE' for c in checks) and result['decision'] == 'PASS':
        raise PlanningFailure('planning_review_invalid', 'failed narrative check cannot pass')
    return {**result, 'checks':deepcopy(checks)}


def patch_script(script: JsonObject, raw: JsonObject) -> JsonObject:
    """Replace existing draft leaves only; the next parse checks text constraints."""
    if set(script) != set(NARRATIVE_SCHEMA['properties']):
        raise PlanningFailure('planning_patch_invalid', 'script shape')
    if set(raw) != {'base_script_sha256', 'edits'} or raw['base_script_sha256'] != canonical_sha256(script):
        raise PlanningFailure('planning_patch_invalid', 'script checksum')
    edits = raw['edits']
    if not isinstance(edits, list) or not 1 <= len(edits) <= 3:
        raise PlanningFailure('planning_patch_invalid', 'script edits')
    result = dict(script)
    seen: set[str] = set()
    for edit in edits:
        if not isinstance(edit, dict) or set(edit) != {'path', 'value'}:
            raise PlanningFailure('planning_patch_invalid', 'script edit shape')
        path = edit['path']
        if path not in ('/master_text', '/delivery', '/emotional_arc') or path in seen:
            raise PlanningFailure('planning_patch_invalid', 'protected script path')
        seen.add(path)
        result[path[1:]] = edit['value']
    if any(not isinstance(v,str) for v in result.values()):
        raise PlanningFailure('planning_patch_invalid', 'script leaf type')
    return result


def validate_proof(plan: JsonObject, proof: JsonObject) -> None:
    """An old generic PASS or a receipt for different words is not narrative QA."""
    parse_script(plan['narrative'])
    review = proof.get('narrative_review')
    if not isinstance(review, dict) or review.get('profile') != PROFILE:
        raise PlanningFailure('creative_provenance_invalid', 'narrative proof missing')
    if review.get('script_sha256') != canonical_sha256(plan['narrative']):
        raise PlanningFailure('creative_provenance_invalid', 'narrative proof mismatch')
    verdict = review_verdict({k:review.get(k) for k in ('decision', 'issues', 'checks')})
    if verdict['decision'] != 'PASS':
        raise PlanningFailure('creative_plan_review_failed', 'narrative rejected')
    digest = review.get('review_request_sha256')
    receipts = proof.get('execution_receipts', [])
    if digest not in proof.get('request_hashes', []) or not any(
        r.get('prompt_sha256') == digest and r.get('execution', {}).get('model') == review.get('reviewer_model')
        for r in receipts
    ):
        raise PlanningFailure('creative_provenance_invalid', 'narrative execution missing')


def validate_visual_proof(plan: JsonObject, proof: JsonObject, result: JsonObject) -> None:
    """Require exact-plan, all-panel and critical-reaction evidence before completion."""
    validate_proof(plan, proof)
    if result.get('narrative_review') != proof['narrative_review']:
        raise PlanningFailure('media_qa_incomplete', 'narrative review mismatch')
    visual = result.get('narrative_visual_review')
    ids = [s['id'] for s in plan['shots']]
    expected_profile=('image-only-storyboard-v2' if plan.get('schema_version')=='3.0'
                      else 'image-only-emotion-v1')
    expected_source=('extracted_storyboard_panels' if plan.get('schema_version')=='3.0'
                     else 'generated_scene_assets')
    if (not isinstance(visual, dict) or visual.get('profile') != expected_profile
            or visual.get('source_stage') != expected_source
            or visual.get('decision') != 'PASS' or visual.get('plan_sha256') != canonical_sha256(plan)
            or visual.get('shot_ids') != ids or visual.get('human_review') != 'NOT_PERFORMED'):
        raise PlanningFailure('media_qa_incomplete', 'visual sequence proof missing')
    evidence = visual.get('evidence_artifact_ids')
    panels = visual.get('source_panels')
    if (not isinstance(evidence, list) or len(evidence) != 2
            or any(not isinstance(x,str) or not re.fullmatch(r'sha256:[0-9a-f]{64}',x) for x in evidence)
            or not isinstance(panels,list) or any(not isinstance(p,dict) for p in panels)
            or [p.get('id') for p in panels] != ids
            or any(not re.fullmatch(r'[0-9a-f]{64}',str(p.get('sha256',''))) for p in panels)):
        raise PlanningFailure('media_qa_incomplete', 'visual source evidence missing')
    reading = visual.get('image_only_reading', {})
    comparison = visual.get('emotion_comparison', {})
    critical_ids = [s['id'] for s in plan['shots'] if s['performance']['critical']]
    if (not isinstance(reading,dict) or not isinstance(comparison,dict)
            or not isinstance(reading.get('readings'),list) or not isinstance(comparison.get('checks'),list)
            or any(not isinstance(r,dict) or any(not isinstance(r.get(k),str) or not r[k].strip()
                for k in ('visible_action','visible_emotion')) for r in reading['readings'])
            or any(not isinstance(c,dict) or not isinstance(c.get('evidence'),str) for c in comparison['checks'])
            or reading.get('decision') != 'PASS' or reading.get('issues') != []
            or [r.get('shot_id') for r in reading['readings']] != ids
            or comparison.get('decision') != 'PASS' or comparison.get('issues') != []
            or not set(critical_ids) <= {c.get('shot_id') for c in comparison['checks']}
            or [c.get('shot_id') for c in comparison['checks']] != [sid for sid in ids if sid in {c.get('shot_id') for c in comparison['checks']}]
            or any(c.get('status') != 'PASS' or len(c['evidence'].strip()) < 10 for c in comparison['checks'])):
        raise PlanningFailure('media_qa_incomplete', 'visual sequence coverage failed')


def draft_review_story(packet: JsonObject, prefix: str, bundle: JsonObject,
                       call: PlanningCall, model: str, review_model: str) -> tuple[NarrativeScript, JsonObject, int]:
    """Review one continuous spoken story before any allocation; share repair budget."""
    from .contract import NARRATIVE_SCHEMA
    from .pi_runtime import _json
    from .planning_contract import MAX_PATCHES
    system = ('Write one continuous Korean spoken story as if explaining a personal anecdote to a friend, '
        'not a literary report or a list of scene captions. Introduce roles before unexplained names; '
        'make wants, hurt, motives, cause-to-choice transitions and emotional payoff understandable by ear. '
        'The narrator is telling a listener WHY each event mattered, not reading a scene list. Use a '
        'consistent natural Korean spoken register, normally third-person conversational retelling; '
        'do not invent first-person eyewitness testimony. Avoid repeated written ~었다/~했다/~습니다 '
        'sentences and repeated character-name sentence starts. Connect clauses naturally across '
        'breaths so cause, misunderstanding, discovery and receiving reactions unfold. A story arc '
        'must survive listening without pictures. Give the listener a chance to anticipate, wonder, '
        'recognize a feeling and receive an answer; do not explain every reaction immediately. '
        'Use a brief natural question or shared recognition only where the event motivates it, '
        'never compulsory audience questions, fabricated audience replies or comment/like bait. '
        'Do not mechanically alternate endings or add filler. '
        'Keep the selected pitch and original-fiction boundaries. Use punctuation for emphasis/breath; '
        'do not write acting labels into spoken words.250-550 non-whitespace Korean characters. '
        'Return only JSON matching: '+_json(NARRATIVE_SCHEMA))
    raw = call(prefix+'-narrative-draft', system, {**packet,'operation':'narrative_draft'}, model)
    candidate = raw
    review_system = ('Review ONLY the continuous spoken story, with no shot/caption design to rescue it. '
        'Reject a report-like list, unexplained roles, unmotivated choices, emotion labels without reasons, '
        'or an ending that resolves procedure but not the protagonist\'s experience. The speech_register '
        'check must cite actual narrator clauses and distinguish spoken retelling from written reportage '
        '(quoted character dialogue does not determine narrator register). A delivery note claiming '
        'conversational style cannot rescue written master_text. Natural storytelling '
        'is not achieved by swapping sentence endings. Listener_orientation also checks whether '
        'the story makes room for anticipation and emotional reception rather than immediately '
        'explaining every beat; tacked-on audience questions are not meaningful interaction. '
        'Do not demand invented testimony or alter the pitch. '
        'Return JSON {decision:PASS|REVISE,issues:[blocking strings],checks:[{id,status:PASS|REVISE,evidence}]}. '
        'Exactly one check per required_check_ids; cite actual wording and explain its listener effect '
        'in evidence10..600chars. PASS only when all checks pass; failed checks require actionable issues. '
        'This is AI_TEXT_REVIEW, not actual audio listening.')
    for attempt in range(MAX_PATCHES+1):
        key = prefix+f'-narrative-review-{attempt}'
        try:
            script = parse_script(candidate)
        except PlanningFailure as exc:
            chars=len(''.join(str(candidate.get('master_text','')).split()))
            checked={'decision':'REVISE','issues':[f'{exc.detail}; master has {chars} non-whitespace characters, required250..550. Preserve causality and conversational delivery.']}
        else:
            checked = review_verdict(call(key, review_system, {**packet,'operation':'narrative_review',
                'narrative':script,'script_sha256':canonical_sha256(script),'required_check_ids':list(CRITERIA)}, review_model))
            if checked['decision'] == 'PASS':
                return script, checked, attempt
        if attempt == MAX_PATCHES:
            raise PlanningFailure('narrative_review_rejected', '; '.join(checked['issues']))
        correction = call(prefix+f'-narrative-patch-{attempt+1}',
            'Repair only the indexed listener problems in this unapproved script. Preserve the selected '
            'pitch, cast, causal facts and payoff. Return {base_script_sha256,edits:[{path,value}]}; '
            'replace1..3 existing leaves /master_text,/delivery,/emotional_arc. No approvals or media tools.',
            {**packet,'operation':'narrative_patch','narrative':candidate,
             'script_sha256':canonical_sha256(candidate),'issues':checked['issues']}, model)
        candidate = patch_script(candidate, correction)
    raise PlanningFailure('narrative_review_rejected')
