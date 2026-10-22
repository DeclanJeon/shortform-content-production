"""Structural validation for the versioned still-image Shorts creative plan."""

from copy import deepcopy
import hashlib
import json
import math
import re


def _obj(properties, required=None):
    return {"type": "object", "properties": properties,
            "required": list(properties) if required is None else required,
            "additionalProperties": False}


def _str(minimum, maximum):
    return {"type": "string", "minLength": minimum, "maxLength": maximum}


def _arr(items, minimum, maximum):
    return {"type": "array", "items": items, "minItems": minimum, "maxItems": maximum}


def _enum(*values):
    return {"type": "string", "enum": list(values), "minLength": min(map(len, values)), "maxLength": max(map(len, values))}

_TEXT = _str(1, 400)
_APPEAR = _obj({k: _str(1, n) for k, n in {
    "silhouette":180,"face":180,"hair":180,"eyes":180,"palette":180,"wardrobe":240}.items()})
_BEAT = _obj({"id":_enum(*[f"B{i:02}" for i in range(1,7)]),
              "function":_enum("hook","setup","development","turn","payoff","closing"),
              "summary":_str(1,400)})
_CHAR = _obj({"id":_enum("CH01","CH02","CH03"), "name":_str(1,40), "role":_str(1,120),
              "goal":_str(1,180), "choice_cost":_str(1,180), "appearance":_APPEAR,
              "mannerism":_str(1,180)})
_SEG = _obj({"id":_enum(*[f'S{i:02}' for i in range(1,25)]), "text":_str(1,90)})
_SHOT = _obj({"id":_enum(*[f'SH{i:02}' for i in range(1,25)]), "beat_id":_enum(*[f"B{i:02}" for i in range(1,7)]),
              "segment_id":_enum(*[f'S{i:02}' for i in range(1,25)]), "character_ids":_arr(_enum('CH01','CH02','CH03'),0,3),
              "action":_str(20,1200), "shot_size":_enum("extreme_close","close","medium","wide","detail","over_shoulder","high_angle","low_angle"),
              "camera_move":_enum("static","push","pull","pan_left","pan_right"),
              "estimated_duration_seconds":{"type":"number","minimum":1,"maximum":4}})
PLAN_SCHEMA = _obj({
    "schema_version":_enum("1.0"), "title":_str(1,80), "logline":_str(1,240), "synopsis":_str(30,1500), "fiction":{"const":True},
    "beats":_arr(_BEAT,6,6), "characters":_arr(_CHAR,1,3), "narration_segments":_arr(_SEG,16,24),
    "shots":_arr(_SHOT,16,24), "estimated_duration_seconds":{"type":"number","exclusiveMinimum":0,"maximum":180},
    "visual_style":_obj({"medium":_enum("2d_storybook_chibi"),"palette":_str(1,240),"lighting":_str(1,240),"mood":_str(1,240)}),
    "publishing":_obj({"description":_str(1,1000),"tags":_arr(_str(1,50),0,10),"contains_synthetic_media":{"const":True},"self_declared_made_for_kids":{"const":False}}),
    "review_notes":_arr(_str(1,300),0,10)})

NARRATIVE_SCHEMA = _obj({
    'master_text': _str(250, 1400),
    'delivery': _str(10, 400),
    'emotional_arc': _str(10, 400),
})
PERFORMANCE_SCHEMA = _obj({
    'emotion_before': _str(1, 120), 'trigger': _str(1, 180),
    'visible_reaction': _str(10, 240), 'emotion_after': _str(1, 120),
    'purpose': _str(10, 180), 'critical': {'type': 'boolean'},
})
PERFORMANCE_SCHEMA['properties']['listening'] = _obj({
    'beat': _enum('flow','expectation','reaction','payoff','closing'),
    'pause_after_seconds': {'type':'number','minimum':0,'maximum':1.2},
    'music_mood': _enum('playful','curious','warm','quiet'),
})
PLAN_SCHEMA_V2 = deepcopy(PLAN_SCHEMA)
PLAN_SCHEMA_V2['properties']['schema_version'] = _enum('2.0')
PLAN_SCHEMA_V2['properties']['narrative'] = NARRATIVE_SCHEMA
PLAN_SCHEMA_V2['required'].append('narrative')
PLAN_SCHEMA_V2['properties']['shots']['items']['properties'].update(
    stage=_str(10, 240), performance=PERFORMANCE_SCHEMA)
PLAN_SCHEMA_V2['properties']['shots']['items']['required'].extend(['stage', 'performance'])

_SCENE_ID = _enum(*[f'SC{i:02}' for i in range(1, 25)])
_PANEL_ID = _enum(*[f'P{i:02}' for i in range(1, 25)])
_SHOT_ID = _enum(*[f'SH{i:02}' for i in range(1, 25)])
_BEAT_ID = _enum(*[f'B{i:02}' for i in range(1, 7)])
_CHAR_ID = _enum(*[f'CH{i:02}' for i in range(1, 4)])
_AUDIO_CUE_ID = _enum(*[f'AC{i:02}' for i in range(1, 25)])
_SPEECH_ID = _enum(*[f'SP{i:02}' for i in range(1, 25)])
_SCENE_INTENT = _obj({
    'id': _SCENE_ID,
    'beat_ids': _arr(_BEAT_ID, 1, 6),
    'purpose': _str(10, 400),
    'entry_knowledge': _str(5, 240),
    'exit_knowledge': _str(5, 240),
    'entry_emotion': _str(5, 160),
    'exit_emotion': _str(5, 160),
    'pov_axis': _str(10, 300),
    'camera_grammar': _str(10, 400),
    'movement_reason': _str(10, 400),
    'neighboring_relation': _str(10, 500),
    'reuse_reason': _str(5, 300),
})
_BOARD_PANEL = _obj({
    'id': _PANEL_ID,
    'shot_id': _SHOT_ID,
    'scene_id': _SCENE_ID,
    'beat_ids': _arr(_BEAT_ID, 1, 6),
    'visible_character_ids': _arr(_CHAR_ID, 0, 3),
    'audio_cue_ids': _arr(_AUDIO_CUE_ID, 1, 1),
    'speech_ids': _arr(_SPEECH_ID, 1, 1),
    'visual_action': _str(10, 500),
    'reveals': _str(5, 300),
    'withholds': _str(5, 300),
    'continuity': _str(10, 400),
})
_STORYBOARD = _obj({
    'purpose': _str(10, 240),
    'recipient': _str(10, 240),
    'usage_stage': _str(10, 240),
    'panels': _arr(_BOARD_PANEL, 16, 24),
})
PLAN_SCHEMA_V3 = deepcopy(PLAN_SCHEMA_V2)
PLAN_SCHEMA_V3['properties']['schema_version'] = _enum('3.0')
PLAN_SCHEMA_V3['properties']['shots']['items']['properties']['performance']['required'].append('listening')
PLAN_SCHEMA_V3['properties']['beats']['items']['properties'].update(
    emotion=_str(5, 240), synopsis_locator=_str(1, 160))
PLAN_SCHEMA_V3['properties']['beats']['items']['required'].extend(
    ['emotion', 'synopsis_locator'])
PLAN_SCHEMA_V3['properties']['scenes'] = _arr(_SCENE_INTENT, 1, 24)
PLAN_SCHEMA_V3['required'].append('scenes')
PLAN_SCHEMA_V3['properties']['storyboard'] = _STORYBOARD
PLAN_SCHEMA_V3['required'].append('storyboard')
PLAN_SCHEMA_V3['properties']['shots']['items']['properties'].update({
    'scene_id': _SCENE_ID,
    'purpose': _str(10, 400),
    'synopsis_locator': _str(1, 160),
    'start_state': _str(10, 300),
    'end_state': _str(10, 300),
    'angle': _str(3, 120),
    'framing': _str(10, 300),
    'focus': _str(5, 200),
    'camera_start': _str(5, 240),
    'camera_end': _str(5, 240),
    'movement_reason': _str(10, 400),
    'spatial_mode': _enum('not_applicable'),
    'spatial_reason': _str(10, 300),
    'vfx_enabled': {'type': 'boolean'},
    'vfx_description': _str(5, 300),
    'contact_evidence': _str(5, 400),
})
PLAN_SCHEMA_V3['properties']['shots']['items']['required'].extend([
    'scene_id', 'purpose', 'synopsis_locator', 'start_state', 'end_state',
    'angle', 'framing', 'focus', 'camera_start', 'camera_end', 'movement_reason',
    'spatial_mode', 'spatial_reason', 'vfx_enabled', 'vfx_description',
    'contact_evidence',
])

_BOILERPLATE = ("이 영상은 허구의 이야기이며 실제 사건을 재현한 것이 아닙니다.",
 "AI 생성 일러스트와 Typecast 합성 음성을 사용했습니다.",
 "빛을 나누는 장면은 이야기 속 표현이며 실제 설치 방법을 안내하지 않습니다.")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_KOREAN = re.compile(r"[가-힣]")


def canonical_sha256(value):
    """Hash JSON using sorted keys, compact separators, and UTF-8."""
    try:
        data = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ValueError("invalid_json_value") from exc
    return hashlib.sha256(data).hexdigest()


def _fail(code):
    raise ValueError(code)


def validate_plan(plan):
    """Validate and return plan unchanged; errors contain only machine codes."""
    def walk(value, schema):
        typ = schema.get("type")
        if typ == "object":
            if not isinstance(value, dict): _fail("type")
            if not set(schema["required"]) <= set(value) <= set(schema["properties"]): _fail("keys")
            for key, sub in schema["properties"].items():
                if key in value: walk(value[key], sub)
        elif typ == "array":
            if not isinstance(value, list): _fail("type")
            if not schema["minItems"] <= len(value) <= schema["maxItems"]: _fail("size")
            for item in value: walk(item, schema["items"])
        elif typ == "string":
            if not isinstance(value, str): _fail("type")
            if not schema["minLength"] <= len(value) <= schema["maxLength"] or not value.strip(): _fail("size")
            if _CONTROL.search(value): _fail("control_char")
        elif typ == "boolean":
            if type(value) is not bool: _fail('type')
        elif typ == "number":
            if isinstance(value, bool) or not isinstance(value, (int,float)): _fail("type")
            if not math.isfinite(value): _fail("number")
            if value < schema.get("minimum", schema.get("exclusiveMinimum", -math.inf)) or value > schema["maximum"] or ("exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]): _fail("range")
        if "enum" in schema and value not in schema["enum"]: _fail("enum")
        if "const" in schema and (type(value) is not type(schema["const"]) or value != schema["const"]): _fail("const")
    version = plan.get('schema_version') if isinstance(plan, dict) else None
    schema = (PLAN_SCHEMA_V3 if version == '3.0' else
              PLAN_SCHEMA_V2 if version == '2.0' else PLAN_SCHEMA)
    walk(plan, schema)
    if version in ('2.0', '3.0'):
        allocated = ' '.join(s['text'].strip() for s in plan['narration_segments'])
        if ' '.join(allocated.split()) != ' '.join(plan['narrative']['master_text'].split()):
            _fail('narrative_allocation_mismatch')
        directions = [s['performance'].get('listening') for s in plan['shots']]
        if any(d is not None for d in directions):
            if any(d is None for d in directions):_fail('listener_direction_incomplete')
            holds = [d['pause_after_seconds'] for d in directions]
            if sum(h>0 for h in holds)<2 or all(h>0 for h in holds) or sum(holds)>12:
                _fail('listener_rhythm_missing')
        critical = [s['performance'] for s in plan['shots'] if s['performance']['critical']]
        if len(critical) < 2 or len({p['visible_reaction'] for p in critical}) < 2:
            _fail('narrative_performance_missing')
    if version == '3.0':
        scenes = plan['scenes']
        scene_ids = [scene['id'] for scene in scenes]
        if scene_ids != [f'SC{i:02}' for i in range(1, len(scenes) + 1)]:
            _fail('scene_ids')
        beats_by_id = {beat['id']: beat for beat in plan['beats']}
        scene_by_id = {scene['id']: scene for scene in scenes}
        for scene in scenes:
            if (len(scene['beat_ids']) != len(set(scene['beat_ids']))
                    or not set(scene['beat_ids']) <= set(beats_by_id)):
                _fail('scene_beat_reference')
        panels = plan['storyboard']['panels']
        if [panel['id'] for panel in panels] != [f'P{i:02}' for i in range(1, len(panels) + 1)]:
            _fail('panel_ids')
        if len(panels) != len(plan['shots']):
            _fail('still_panel_coverage')
        used_scenes, used_beats = set(), set()
        for shot, panel in zip(plan['shots'], panels):
            scene_id = shot['scene_id']
            if scene_id not in scene_by_id or panel['scene_id'] != scene_id:
                _fail('shot_scene_reference')
            if panel['shot_id'] != shot['id'] or panel['beat_ids'] != [shot['beat_id']]:
                _fail('panel_shot_reference')
            if (len(panel['visible_character_ids']) != len(set(panel['visible_character_ids']))
                    or not set(panel['visible_character_ids']) <= set(shot['character_ids'])):
                _fail('panel_character_reference')
            if any(character not in {c['id'] for c in plan['characters']}
                   for character in panel['visible_character_ids']):
                _fail('panel_character_reference')
            if panel['audio_cue_ids'] != [f"AC{int(shot['id'][2:]):02}"] \
                    or panel['speech_ids'] != [f"SP{int(shot['id'][2:]):02}"]:
                _fail('panel_audio_speech_reference')
            if shot['beat_id'] not in scene_by_id[scene_id]['beat_ids']:
                _fail('shot_scene_beat_reference')
            if shot['synopsis_locator'] != f"synopsis.md#{shot['beat_id']}":
                _fail('synopsis_locator')
            if shot['start_state'] != shot['end_state']:
                _fail('still_shot_state_change')
            used_scenes.add(scene_id)
            used_beats.add(shot['beat_id'])
        for scene in scenes:
            if scene['id'] not in used_scenes:
                _fail('unused_scene')
            if not set(scene['beat_ids']) <= used_beats:
                _fail('unused_scene_beat')
        if used_beats != set(beats_by_id):
            _fail('storyboard_beat_coverage')
    if len(plan["shots"]) != len(plan["narration_segments"]): _fail("timing_mismatch")
    for i, (b, function) in enumerate(zip(plan["beats"], ("hook","setup","development","turn","payoff","closing")),1):
        if b["id"] != f"B{i:02}" or b["function"] != function: _fail("beats")
    if [c["id"] for c in plan["characters"]] != [f"CH{i:02}" for i in range(1,len(plan["characters"])+1)]: _fail("ids")
    names = [c["name"].strip().casefold() for c in plan["characters"]]
    if len(names) != len(set(names)): _fail("duplicate_id")
    traits = ("silhouette","face","hair","eyes","palette","wardrobe")
    for i,c in enumerate(plan["characters"]):
        for other in plan["characters"][:i]:
            diff = sum(" ".join(c["appearance"][t].casefold().split()) != " ".join(other["appearance"][t].casefold().split()) for t in traits)
            if diff < 3: _fail("identity_similarity")
    if [s["id"] for s in plan["narration_segments"]] != [f"S{i:02}" for i in range(1,len(plan["narration_segments"])+1)]: _fail("ids")
    if [s["id"] for s in plan["shots"]] != [f"SH{i:02}" for i in range(1,len(plan["shots"])+1)]: _fail("ids")
    if len({' '.join(s["action"].casefold().split()) for s in plan["shots"]}) != len(plan["shots"]): _fail("duplicate_action")
    if any(len(s['character_ids']) != len(set(s['character_ids'])) for s in plan['shots']): _fail('duplicate_character_reference')
    segment_ids = [s["segment_id"] for s in plan["shots"]]
    if segment_ids != [s["id"] for s in plan["narration_segments"]]: _fail("segment_reference")
    char_ids = {c["id"] for c in plan["characters"]}
    beats = [int(s["beat_id"][1:]) for s in plan["shots"]]
    if any(not set(s["character_ids"]) <= char_ids for s in plan["shots"]): _fail("character_reference")
    if beats != sorted(beats) or set(beats) != set(range(1,7)) or beats[0] != 1 or beats[-1] != 6: _fail("beat_coverage")
    if len({s["shot_size"] for s in plan["shots"]}) < 4: _fail("shot_variety")
    narration = "".join(s["text"] for s in plan["narration_segments"])
    compact = "".join(narration.split())
    if not 250 <= len(compact) <= 550: _fail("narration_length")
    if len(_KOREAN.findall(compact)) < len(compact)/2: _fail("korean_ratio")
    desc = plan["publishing"]["description"]
    if any(x in desc for x in _BOILERPLATE): _fail("excluded_boilerplate")
    durations = [s["estimated_duration_seconds"] for s in plan["shots"]]
    if abs(plan["estimated_duration_seconds"] - sum(durations)) > 0.05: _fail("timing_mismatch")
    return plan
