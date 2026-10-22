"""Canonical planning context, ordered assembly and constrained local edits.

Only deterministic structure is synthesized. Story words/order are never silently
sorted or rewritten; all output still needs the normal semantic/rights gates.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Final, TypeAlias
import re

from .contract import PLAN_SCHEMA, PLAN_SCHEMA_V2, PLAN_SCHEMA_V3, canonical_sha256

Json: TypeAlias = str | int | float | bool | None | list['Json'] | dict[str, 'Json']
JsonObject: TypeAlias = dict[str, Json]
POLICY: Final = 'planning-recovery-v2'
MAX_CALLS: Final = 15
MAX_CANDIDATES: Final = 2
MAX_PATCHES: Final = 2


class PlanningFailure(ValueError):
    """A confirmed planning result is unusable, not an unknown provider outcome."""
    def __init__(self, code: str, detail: str = '') -> None:
        self.code = code
        self.detail = detail
        super().__init__(code)


def grouped_schema(narrative_first: bool = False) -> JsonObject:
    """Derive allocation schema; the program injects the already reviewed story."""
    schema = deepcopy(PLAN_SCHEMA_V3)
    if narrative_first:
        del schema['properties']['narrative']
        schema['required'].remove('narrative')
    props = schema['properties']
    shot = deepcopy(props['shots']['items'])
    for key in ('id', 'beat_id', 'segment_id'):
        del shot['properties'][key]
        shot['required'].remove(key)
    beat = props['beats']['items']
    del beat['properties']['synopsis_locator']
    beat['required'].remove('synopsis_locator')
    del props['storyboard']
    schema['required'].remove('storyboard')
    panel_details = deepcopy(PLAN_SCHEMA_V3['properties']['storyboard']['properties']['panels']['items'])
    for key in ('id', 'shot_id', 'scene_id', 'beat_ids', 'audio_cue_ids', 'speech_ids'):
        del panel_details['properties'][key]
        panel_details['required'].remove(key)
    panel = {'type':'object','additionalProperties':False,
             'properties':{
                 'narration':deepcopy(props['narration_segments']['items']['properties']['text']),
                 'shot':shot,'board_panel':panel_details},
             'required':['narration','shot','board_panel']}
    beat['properties']['panels'] = {'type':'array','items':panel,'minItems':1,'maxItems':24}
    beat['required'].append('panels')
    for key in ('shots', 'narration_segments'):
        del props[key]
        schema['required'].remove(key)
    return schema


def assemble_grouped(raw: JsonObject) -> JsonObject:
    """Generate contiguous references from beat-local panels; preserve story order."""
    plan = deepcopy(raw)
    version = raw.get('schema_version')
    groups = plan.get('beats')
    if not isinstance(groups, list) or len(groups) != 6:
        raise PlanningFailure('grouped_plan_invalid', 'six beats required')
    beats = []
    shots = []
    segments = []
    for i, group in enumerate(groups, 1):
        expected_group = {'id','function','summary','panels'}
        if version == '3.0':
            expected_group.add('emotion')
        if not isinstance(group, dict) or set(group) != expected_group:
            raise PlanningFailure('grouped_plan_invalid', 'beat shape')
        panels = group['panels']
        if not isinstance(panels, list) or not panels:
            raise PlanningFailure('grouped_plan_invalid', 'missing beat panels')
        bid = f'B{i:02}'
        beat = {'id':bid,'function':('hook','setup','development','turn','payoff','closing')[i-1],
                'summary':group['summary']}
        if version == '3.0':
            beat.update(emotion=group['emotion'],synopsis_locator=f'synopsis.md#{bid}')
        beats.append(beat)
        for panel in panels:
            expected_panel = {'narration','shot','board_panel'} if version == '3.0' else {'narration','shot'}
            if not isinstance(panel, dict) or set(panel) != expected_panel:
                raise PlanningFailure('grouped_plan_invalid', 'panel shape')
            shot = panel['shot']
            schema = (PLAN_SCHEMA_V3 if version == '3.0' else
                      PLAN_SCHEMA_V2 if version == '2.0' else PLAN_SCHEMA)
            expected = set(schema['properties']['shots']['items']['properties']) - {'id','beat_id','segment_id'}
            if not isinstance(shot, dict) or set(shot) != expected:
                raise PlanningFailure('grouped_plan_invalid', 'shot shape')
            n = len(shots) + 1
            segments.append({'id':f'S{n:02}','text':panel['narration']})
            shot_row = {**shot,'id':f'SH{n:02}','beat_id':bid,'segment_id':f'S{n:02}'}
            if version == '3.0':
                shot_row['synopsis_locator'] = f'synopsis.md#{bid}'
                shots.append(shot_row)
                plan.setdefault('_board_panels', []).append({
                    'id':f'P{n:02}','shot_id':shot_row['id'],'scene_id':shot_row['scene_id'],
                    'beat_ids':[bid],'audio_cue_ids':[f'AC{n:02}'],
                    'speech_ids':[f'SP{n:02}'],**panel['board_panel']})
            else:
                shots.append(shot_row)
    if version == '3.0':
        board_panels = plan.pop('_board_panels')
        plan['storyboard'] = {
            'purpose':'Full production board for a Korean still-image story.',
            'recipient':'Creative coordinator, visual QA, and still-image renderer.',
            'usage_stage':'Image-backed storyboard and ordered short-form production.',
            'panels':board_panels,
        }
    plan.update(beats=beats, shots=shots, narration_segments=segments)
    return plan


def patch_diagnostics(plan: JsonObject, errors: list[str]) -> list[str]:
    """Locate schema failures without asking a model to guess an enum/length code."""
    issues=[]
    def walk(value, schema, path):
        if 'enum' in schema and value not in schema['enum']:
            issues.append(f'{path}: value {value!r} must be one of {schema["enum"]}')
        if isinstance(value,str) and (len(value)<schema.get('minLength',0) or len(value)>schema.get('maxLength',1000000)):
            issues.append(f'{path}: text length {len(value)} outside {schema.get("minLength",0)}..{schema.get("maxLength",1000000)}')
        if isinstance(value,dict):
            for key,child in schema.get('properties',{}).items():
                if key in value:walk(value[key],child,path+'/'+key)
        if isinstance(value,list) and 'items' in schema:
            for i,child in enumerate(value):walk(child,schema['items'],path+'/'+str(i))
    version=plan.get('schema_version')
    schema=PLAN_SCHEMA_V3 if version=='3.0' else PLAN_SCHEMA_V2 if version=='2.0' else PLAN_SCHEMA
    walk(plan,schema,'')
    if 'narration_length' in errors:
        n=sum(len(''.join(x['text'].split())) for x in plan['narration_segments'])
        issues.append(f'narration_length: {n} non-whitespace characters, required250..550; adjust existing narration text leaves, preserve story causality')
    return [x[:400] for x in (issues or errors)][:12]


PATCH_PATH: Final = re.compile(
    r'/(?:title|logline|synopsis|review_notes|publishing/(?:description|tags)'
    r'|beats/[0-5]/(?:summary|emotion)|scenes/[0-9]{1,2}/(?:beat_ids|purpose|entry_knowledge|exit_knowledge|entry_emotion|exit_emotion|pov_axis|camera_grammar|movement_reason|neighboring_relation|reuse_reason)'
    r'|narration_segments/[0-9]{1,2}/text'
    r'|shots/[0-9]{1,2}/(?:action|stage|character_ids|shot_size|camera_move|estimated_duration_seconds'
    r'|performance/(?:emotion_before|trigger|visible_reaction|emotion_after|purpose|critical)'
    r'|scene_id|purpose|synopsis_locator|start_state|end_state|angle|framing|focus|camera_start|camera_end|movement_reason|spatial_reason|vfx_enabled|vfx_description|contact_evidence)'
    r'|storyboard/panels/[0-9]{1,2}/(?:visual_action|reveals|withholds|continuity))\Z')


def apply_patch(plan: JsonObject, response: JsonObject) -> JsonObject:
    """Apply only existing editable leaves to the exact rejected plan version."""
    if (set(response) != {'base_plan_sha256','edits'}
            or response['base_plan_sha256'] != canonical_sha256(plan)):
        raise PlanningFailure('planning_patch_invalid', 'base checksum')
    edits = response['edits']
    if not isinstance(edits, list) or len(edits) > 12:
        raise PlanningFailure('planning_patch_invalid', 'edit count')
    result = deepcopy(plan)
    seen = set()
    for edit in edits:
        if not isinstance(edit, dict) or set(edit) != {'path','value'}:
            raise PlanningFailure('planning_patch_invalid', 'edit shape')
        path = edit['path']
        if not isinstance(path, str) or not PATCH_PATH.fullmatch(path) or path in seen:
            raise PlanningFailure('planning_patch_invalid', 'protected path')
        seen.add(path)
        parts = path.lstrip('/').split('/')
        target = result
        try:
            for part in parts[:-1]:
                target = target[int(part)] if isinstance(target, list) else target[part]
            key = int(parts[-1]) if isinstance(target, list) else parts[-1]
            target[key]  # Replacement only; never insert/delete/reorder arrays.
            target[key] = deepcopy(edit['value'])
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise PlanningFailure('planning_patch_invalid', 'unknown path') from exc
    return result


def candidate_order(selection: JsonObject, locked_pitch: str | None = None) -> list[JsonObject]:
    """Bound alternate pitches to the same accepted source/day admission."""
    candidates = selection['candidates']
    eligible = [p for p in candidates if p['scores']['originality'] >= 3 and p['scores']['feasibility'] >= 3]
    eligible.sort(key=lambda p:(-(3*p['scores']['originality']+2*p['scores']['channel_fit']+2*p['scores']['feasibility']),p['estimated_image_count'],int(p['id'][1:])))
    if locked_pitch is not None:
        eligible = [p for p in eligible if p['id'] == locked_pitch]
    if not eligible:
        raise PlanningFailure('planning_candidates_exhausted', 'no eligible locked pitch')
    return eligible[:1 if locked_pitch else MAX_CANDIDATES]
