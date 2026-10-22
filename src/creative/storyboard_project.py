"""Project-ledger projection for full, versioned still-image storyboards."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from .contract import validate_plan


def _digest(root: Path, relative: str) -> tuple[str, int]:
    path = root / relative
    if path.is_symlink() or not path.resolve().is_relative_to(root) or not path.is_file():
        raise ValueError('storyboard_source_file_unavailable')
    raw = path.read_bytes()
    return hashlib.sha256(raw).hexdigest(), len(raw)


def _upsert(rows: list[dict], row: dict) -> None:
    for index, current in enumerate(rows):
        if current.get('id') == row['id']:
            rows[index] = row
            return
    rows.append(row)


def _frame_intervals(plan: dict, fps: int = 30) -> tuple[list[tuple[float, float]], float]:
    cursor = 0
    intervals = []
    for shot in plan['shots']:
        frames = round(shot['estimated_duration_seconds'] * fps)
        if frames < 1:
            raise ValueError('storyboard_shot_duration_below_frame')
        start = cursor
        cursor += frames
        intervals.append((start / fps, cursor / fps))
    duration = cursor / fps
    if duration > 180:
        raise ValueError('storyboard_estimated_duration_over_limit')
    return intervals, duration


def project_from_plan(project: dict, plan: dict, specs: list[dict], root: Path,
                      plan_version: int) -> dict:
    """Map the validated creative plan to the canonical production ledger."""
    validate_plan(plan)
    if plan.get('schema_version') != '3.0':
        raise ValueError('full_storyboard_schema_required')
    if type(plan_version) is not int or not 1 <= plan_version <= 20:
        raise ValueError('invalid_plan_version')
    root = Path(root).resolve(strict=True)
    version = f'v{plan_version}'
    intervals, target_duration = _frame_intervals(plan)
    segment_by_id = {row['id']: row['text'] for row in plan['narration_segments']}
    panel_by_shot = {row['shot_id']: row for row in plan['storyboard']['panels']}
    spec_by_id = {row['asset_id']: row for row in specs}
    if set(spec_by_id) != ({row['id'] for row in plan['characters']}
                           | {row['id'] for row in plan['shots']}):
        raise ValueError('storyboard_asset_spec_mismatch')

    plan_path = f'00_MANIFEST/creative-plan-{version}.json'
    synopsis_path = f'02_STORY/synopsis-{version}.md'
    look_path = f'04_CHARACTER_BIBLE/style-world-bible-{version}.md'
    synopsis_asset = f'SYNOPSIS-MD-{version}'
    look_asset = f'LOOK-{version}'
    plan_asset = f'PLANJSON-{version}'
    synopsis_artifact = f'SYNOPSIS-{version}'
    plan_artifact = f'CREATIVE-PLAN-{version}'
    look_artifact = f'STYLE-WORLD-BIBLE-{version}'
    voice_artifact = f'VOICE-PROFILE-{version}'
    board_artifact = f'STORYBOARD-{version}'

    assets = list(project.get('asset_registry') or [])
    artifacts = list(project.get('artifacts') or [])
    spec_versions = {row['asset_id']: f"v{row.get('revision', 1)}" for row in specs}
    for row in artifacts:
        if (row.get('type') in ('creative_plan', 'synopsis', 'style_world_bible',
                                'character_sheet', 'storyboard')
                and row.get('version') != version
                and row.get('status') not in ('stale', 'draft')):
            row['status'] = 'stale'

    def file_asset(asset_id: str, kind: str, path: str, entity_type: str | None = None) -> dict:
        digest, size = _digest(root, path)
        return {
            'id': asset_id, 'kind': kind, 'version': version, 'status': 'available',
            'path': path, 'sha256': digest, 'size_bytes': size,
            **({'entity_type': entity_type} if entity_type else {}),
            'authority': 'derived',
        }

    _upsert(assets, file_asset(plan_asset, 'creative_plan',
                               plan_path))
    _upsert(assets, file_asset(synopsis_asset, 'synopsis_document',
                               synopsis_path))
    _upsert(assets, file_asset(look_asset, 'style_world_bible',
                               look_path, 'look'))
    for spec in specs:
        previous = next((row for row in assets if row.get('id') == spec['asset_id']), None)
        if previous and previous.get('status') in ('available', 'verified') \
                and previous.get('version') != version:
            raise ValueError('storyboard_asset_version_conflict')
        _upsert(assets, {
            'id': spec['asset_id'], 'kind': spec['kind'],
            'version': spec_versions[spec['asset_id']],
            'status': 'planned', 'path': spec['relative_path'],
            'source_asset_ids': list(spec.get('reference_ids') or []),
            **({'entity_type': 'character', 'entity_id': spec['asset_id']}
               if spec['kind'] == 'character_image' else {}),
            'authority': 'derived',
        })
    for character in plan['characters']:
        persona_asset = f"PERSONA-{character['id']}-{version}"
        persona_path = f"04_CHARACTER_BIBLE/{character['id']}-{version}.md"
        _upsert(assets, file_asset(persona_asset, 'character_ssot', persona_path,
                                   'character'))
        character_artifact = {
            'id': f"CHARACTER-SHEET-{character['id']}-{version}",
            'type': 'character_sheet', 'version': version, 'status': 'draft',
            'asset_ids': [persona_asset, character['id']],
            'dependencies': [plan_artifact],
            'dependency_versions': {plan_artifact: version},
        }
        _upsert(artifacts, character_artifact)

    characters = []
    for character in plan['characters']:
        characters.append({
            **character,
            'locked_traits': {
                'role': character['role'], 'goal': character['goal'],
                'choice_cost': character['choice_cost'],
                'appearance': character['appearance'],
                'mannerism': character['mannerism'],
            },
            'persona_artifact_id': f"CHARACTER-SHEET-{character['id']}-{version}",
            'identity_asset_id': character['id'],
        })

    scene_rows = []
    for scene in plan['scenes']:
        shot_ids = [shot['id'] for shot in plan['shots']
                    if shot['scene_id'] == scene['id']]
        intent = {
            'shot_ids': shot_ids,
            'purpose': scene['purpose'],
            'entry_knowledge': scene['entry_knowledge'],
            'exit_knowledge': scene['exit_knowledge'],
            'entry_emotion': scene['entry_emotion'],
            'exit_emotion': scene['exit_emotion'],
            'pov_axis': scene['pov_axis'],
            'camera_grammar': scene['camera_grammar'],
            'movement_reason': scene['movement_reason'],
            'neighboring_relation': scene['neighboring_relation'],
            'reuse_reason': scene['reuse_reason'],
            'status': 'planned',
        }
        scene_rows.append({
            'id': scene['id'], 'purpose': scene['purpose'],
            'beat_ids': list(scene['beat_ids']),
            'camera_intent': intent,
        })

    shots = []
    cues = []
    board_panels = []
    for index, (shot, interval) in enumerate(zip(plan['shots'], intervals), 1):
        start, end = interval
        segment = segment_by_id[shot['segment_id']]
        panel = panel_by_shot[shot['id']]
        voice_id = f"SP{index:02}"
        audio_id = f"AC{index:02}"
        asset_ids = [shot['id'], *shot['character_ids'], look_asset]
        performance = json.dumps({
            'delivery': plan['narrative']['delivery'],
            'shot_performance': shot['performance'],
        }, ensure_ascii=False, sort_keys=True)
        shots.append({
            'id': shot['id'], 'scene_id': shot['scene_id'],
            'beat_ids': [shot['beat_id']], 'character_ids': list(shot['character_ids']),
            'asset_ids': list(dict.fromkeys(asset_ids)), 'claim_ids': ['CL-FICTION'],
            'purpose': shot['purpose'],
            'synopsis_locator': f"02_STORY/synopsis-{version}.md#{shot['beat_id']}",
            'start_state': shot['start_state'], 'end_state': shot['end_state'],
            'start_s': start, 'end_s': end, 'source_duration_s': end - start,
            'source_duration_status': 'estimated', 'playback_rate': 1, 'source_in_s': 0,
            'retry_budget': 1,
            'camera': {
                'shot_size': shot['shot_size'], 'angle': shot['angle'],
                'framing': shot['framing'], 'movement': shot['camera_move'],
                'start': shot['camera_start'], 'end': shot['camera_end'],
                'reason': shot['movement_reason'],
            },
            'spatial': {'mode': 'not_applicable', 'reason': shot['spatial_reason']},
            'vfx': {'enabled': shot['vfx_enabled'], 'description': shot['vfx_description']},
            'speech': [{
                'id': voice_id, 'kind': 'narration', 'character_id': None,
                'text': segment, 'start_s': start, 'end_s': end,
                'voice_artifact_id': voice_artifact, 'lip_sync': 'not_applicable',
                'performance': performance,
            }],
            'audio_cue_ids': [audio_id],
        })
        cues.append({
            'id': audio_id, 'layer': 'voiceover', 'start_s': start, 'end_s': end,
            'shot_id': shot['id'], 'source': shot['segment_id'],
        })
        board_panels.append({
            **panel, 'frame_time_s': start, 'role': 'hold',
            'image_asset_id': shot['id'],
            'asset_version_refs': {
                asset_id: spec_versions.get(asset_id, version) for asset_id in asset_ids
            },
        })

    synopsis_beats = [{
        'id': beat['id'], 'synopsis_locator': f"02_STORY/synopsis-{version}.md#{beat['id']}",
        'event': beat['summary'], 'emotion': beat['emotion'],
    } for beat in plan['beats']]
    synopsis_asset_ids = [synopsis_asset]
    look_asset_ids = [look_asset]
    character_artifacts = [f"CHARACTER-SHEET-{character['id']}-{version}"
                           for character in plan['characters']]
    _upsert(artifacts, {
        'id': plan_artifact, 'type': 'creative_plan', 'version': version,
        'status': 'generated', 'asset_ids': [plan_asset],
        'dependencies': [], 'dependency_versions': {},
    })
    _upsert(artifacts, {
        'id': synopsis_artifact, 'type': 'synopsis', 'version': version,
        'status': 'generated', 'asset_ids': synopsis_asset_ids,
        'dependencies': [plan_artifact], 'dependency_versions': {plan_artifact: version},
    })
    _upsert(artifacts, {
        'id': look_artifact, 'type': 'style_world_bible', 'version': version,
        'status': 'generated', 'asset_ids': look_asset_ids,
        'dependencies': [plan_artifact], 'dependency_versions': {plan_artifact: version},
    })
    _upsert(artifacts, {
        'id': voice_artifact, 'type': 'voice_profile', 'version': version,
        'status': 'draft', 'dependencies': [plan_artifact],
        'dependency_versions': {plan_artifact: version},
    })
    board_dependencies = [plan_artifact, synopsis_artifact, look_artifact,
                          voice_artifact, *character_artifacts]
    _upsert(artifacts, {
        'id': board_artifact, 'type': 'storyboard', 'version': version,
        'status': 'draft', 'finality': 'preliminary',
        'asset_ids': [], 'dependencies': board_dependencies,
        'dependency_versions': {artifact_id: version for artifact_id in board_dependencies},
    })
    project.update({
        'schema_version': '1.1',
        'creative_plan_schema_version': plan['schema_version'],
        'version': f'v{plan_version:03}',
        'plan_version': plan_version,
        'target_duration_s': target_duration, 'fps': 30, 'aspect_ratio': '9:16',
        'audio_mode': 'dialogue', 'look_asset_id': look_asset,
        'scenes': scene_rows, 'characters': characters, 'shots': shots,
        'claims': [{
            'id': 'CL-FICTION', 'statement': 'Original fictional narrative; not factual testimony.',
            'kind': 'fiction', 'status': 'fiction', 'shot_ids': [shot['id'] for shot in shots],
        }],
        'artifacts': artifacts, 'asset_registry': assets, 'audio_cues': cues,
        'storyboard': {
            **plan['storyboard'], 'synopsis_artifact_id': synopsis_artifact,
            'beats': synopsis_beats, 'panels': board_panels,
            'scene_camera_intent': [
                {'scene_id': scene['id'], **scene['camera_intent']} for scene in scene_rows
            ],
        },
        'preproduction': {
            'mode': 'text', 'synopsis_artifact_id': synopsis_artifact,
            'storyboard_artifact_id': board_artifact,
        },
    })
    return project


def _revision_asset_id(asset_id: str, attempt: int) -> str:
    return asset_id if attempt == 1 else f'{asset_id}-v{attempt}'


def update_project_for_media(project: dict, plan: dict, specs: list[dict], root: Path,
                             aligned_shots: list[dict], plan_version: int) -> dict:
    """Replace planned still assets and estimated times with actual media evidence."""
    root = Path(root).resolve(strict=True)
    intervals = {row.get('shot_id'): row for row in aligned_shots
                 if isinstance(row, dict) and isinstance(row.get('shot_id'), str)}
    if set(intervals) != {shot['id'] for shot in plan['shots']}:
        raise ValueError('storyboard_alignment_coverage_mismatch')
    registry_rows=project.setdefault('asset_registry',[])
    repair_path=root/'00_MANIFEST/.media-runtime/repair-index.json'
    repair_index=json.loads(repair_path.read_text(encoding='utf-8')) if repair_path.is_file() else {}
    spec_by_id={spec['asset_id']:spec for spec in specs}
    histories={}
    current_ids={}
    for spec in specs:
        aid=spec['asset_id'];revision=spec.get('revision',1)
        if type(revision) is not int or revision<1:
            raise ValueError('storyboard_asset_revision_invalid')
        index_entry=repair_index.get(aid,{})
        records=[dict(row) for row in (spec.get('attempts') or index_entry.get('attempts') or [])
                 if isinstance(row,dict)]
        if not records:
            prompt_relative=spec.get(
                'prompt_relative_path',f"06_GENERATION_SPECS/{aid}-prompt-v{plan_version}.txt")
            prompt_sha,_=_digest(root,prompt_relative)
            references=[_revision_asset_id(ref,spec_by_id[ref].get('revision',1))
                        for ref in spec.get('reference_ids',[]) if ref in spec_by_id]
            records=[{
                'attempt':revision,'asset_id':_revision_asset_id(aid,revision),
                'relative_path':spec['relative_path'],'prompt_relative_path':prompt_relative,
                'prompt_sha256':prompt_sha,'source_asset_ids':references,
                'result':'conditional','observed_failures':[],'changes':[],
                'preserve':['selected story','cast identity','shot order'],
            }]
        if not any(row.get('attempt')==revision for row in records):
            prompt_relative=spec.get(
                'prompt_relative_path',f"06_GENERATION_SPECS/{aid}-prompt-v{plan_version}.txt")
            prompt_sha,_=_digest(root,prompt_relative)
            references=[_revision_asset_id(ref,spec_by_id[ref].get('revision',1))
                        for ref in spec.get('reference_ids',[]) if ref in spec_by_id]
            records.append({
                'attempt':revision,'asset_id':_revision_asset_id(aid,revision),
                'relative_path':spec['relative_path'],'prompt_relative_path':prompt_relative,
                'prompt_sha256':prompt_sha,'source_asset_ids':references,
                'result':'conditional','observed_failures':[],'changes':[],
                'preserve':['selected story','cast identity','shot order'],
            })
        for record in records:
            if (type(record.get('attempt')) is not int or record['attempt']<1
                    or record.get('result') not in ('accept','reject','conditional')):
                raise ValueError('storyboard_attempt_record_invalid')
            record.setdefault('asset_id',_revision_asset_id(aid,record['attempt']))
        histories[aid]=records
        current_ids[aid]=_revision_asset_id(aid,revision)

    active_attempt_prefix=f'p{plan_version}-'
    referenced_assets=set(current_ids.values())
    for records in histories.values():
        for record in records:
            referenced_assets.update(
                value for value in record.get('source_asset_ids',[]) if isinstance(value,str))
    for asset in registry_rows:
        if (asset.get('kind') in ('scene_image','character_image')
                and str(asset.get('version','')).startswith('p')
                and not str(asset.get('version','')).startswith(active_attempt_prefix)
                and asset.get('id') not in referenced_assets):
            asset['status']='stale'

    generation_attempts=[]
    for spec in specs:
        aid=spec['asset_id']
        for record in histories[aid]:
            number=record['attempt']
            relative=(spec['relative_path'] if number==spec.get('revision',1)
                      else record.get('relative_path'))
            digest,size=_digest(root,relative)
            prompt_relative=record.get('prompt_relative_path',spec.get(
                'prompt_relative_path',f"06_GENERATION_SPECS/{aid}-prompt-v{plan_version}.txt"))
            prompt_sha,_=_digest(root,prompt_relative)
            if record.get('prompt_sha256') and record['prompt_sha256']!=prompt_sha:
                raise ValueError('storyboard_attempt_prompt_drift')
            asset_id=_revision_asset_id(aid,number)
            record.update(asset_id=asset_id,relative_path=relative,
                          prompt_relative_path=prompt_relative,prompt_sha256=prompt_sha)
            source_ids=record.get('source_asset_ids')
            if not isinstance(source_ids,list):
                source_ids=[current_ids[ref] for ref in spec.get('reference_ids',[])
                            if ref in current_ids]
            asset={
                'id':asset_id,'kind':spec['kind'],'version':f'p{plan_version}-a{number}',
                'status':'available','path':relative,'sha256':digest,'size_bytes':size,
                'source_asset_ids':list(source_ids),'authority':'derived',
                'prompt_relative_path':prompt_relative,'prompt_sha256':prompt_sha,
                'generation_outcome':record['result'],
                'generation_attempt':number,
                'observed_failures':list(record.get('observed_failures') or []),
                'changes':list(record.get('changes') or []),
                'preserve':list(record.get('preserve') or []),
            }
            if spec['kind']=='character_image':
                asset.update(entity_type='character',entity_id=aid)
            if record.get('failure_class'):
                asset['failure_class']=record['failure_class']
            if record.get('review_evidence_sha256'):
                asset['review_evidence_sha256']=record['review_evidence_sha256']
            if number>1:
                parent_id=_revision_asset_id(aid,number-1)
                parent=next((row for row in registry_rows if row.get('id')==parent_id),None)
                if parent is not None:
                    asset.update(role='derivative',
                                 master_asset_ref={'asset_id':parent_id,
                                                   'version':parent['version']})
            _upsert(registry_rows,asset)
            if spec['kind']=='scene_image':
                attempt_row={
                    'id':f"GEN-{aid}-A{number:02}",'shot_id':aid,'attempt':number,
                    'route':'codex-imagen','result':record['result'],
                    'result_asset_id':asset_id,'changes':list(record.get('changes') or []),
                    'observed_failures':list(record.get('observed_failures') or []),
                    'preserve':list(record.get('preserve') or []),
                    'prompt_relative_path':prompt_relative,'prompt_sha256':prompt_sha,
                    'source_asset_ids':list(source_ids),
                }
                if record.get('failure_class'):
                    attempt_row['failure_class']=record['failure_class']
                if record.get('review_evidence_sha256'):
                    attempt_row['review_evidence_sha256']=record['review_evidence_sha256']
                generation_attempts.append(attempt_row)

    registry={row['id']:row for row in registry_rows
              if isinstance(row,dict) and isinstance(row.get('id'),str)}

    shots = {shot['id']: shot for shot in project['shots']}
    look_asset=project['look_asset_id']
    for shot_id,row in shots.items():
        if shot_id in current_ids:
            row['asset_ids']=list(dict.fromkeys([
                current_ids[shot_id],
                *[current_ids.get(char_id,char_id) for char_id in row.get('character_ids',[])],
                look_asset,
            ]))
    for character in project.get('characters',[]):
        current_character=current_ids.get(character['id'],character.get('identity_asset_id'))
        character['identity_asset_id']=current_character
        persona_id=character.get('persona_artifact_id')
        artifact=next((item for item in project['artifacts'] if item.get('id')==persona_id),None)
        if artifact and current_character:
            persona_asset=next((asset['id'] for asset in registry_rows
                                if asset.get('kind')=='character_ssot'
                                and asset.get('path')==f"04_CHARACTER_BIBLE/{character['id']}-v{plan_version}.md"),None)
            artifact['asset_ids']=[value for value in (persona_asset,current_character) if value]
            artifact['status']='generated'
    for shot_id, timing in intervals.items():
        start, end = timing.get('start_seconds'), timing.get('end_seconds')
        if (isinstance(start, bool) or not isinstance(start, (int, float))
                or isinstance(end, bool) or not isinstance(end, (int, float))
                or end <= start):
            raise ValueError('storyboard_alignment_interval_invalid')
        row = shots[shot_id]
        row.update(start_s=float(start), end_s=float(end),
                   source_duration_s=float(end-start), source_duration_status='measured')
        for speech in row.get('speech', []):
            speech.update(start_s=float(start), end_s=float(end))
        for cue_id in row.get('audio_cue_ids', []):
            cue = next((value for value in project['audio_cues']
                        if value.get('id') == cue_id), None)
            if cue is None:
                raise ValueError('storyboard_audio_cue_missing')
            cue.update(start_s=float(start), end_s=float(end))
    shot_map = {shot['id']: shot for shot in project['shots']}
    panels = {panel['shot_id']: panel for panel in project['storyboard']['panels']}
    if set(panels) != set(shot_map):
        raise ValueError('storyboard_panel_alignment_mismatch')
    for shot_id, panel in panels.items():
        panel['frame_time_s'] = shot_map[shot_id]['start_s']
        panel['image_asset_id']=current_ids[shot_id]
        panel['asset_version_refs']={asset_id:registry[asset_id]['version']
                                     for asset_id in shot_map[shot_id]['asset_ids']}
        panel.pop('extracted_asset_id',None)
    ordered = sorted(project['shots'], key=lambda row: row['start_s'])
    if not ordered or abs(ordered[0]['start_s']) > 1e-6:
        raise ValueError('storyboard_alignment_must_start_at_zero')
    for before, after in zip(ordered, ordered[1:]):
        if abs(before['end_s']-after['start_s']) > 1e-6:
            raise ValueError('storyboard_alignment_gap_or_overlap')
    project['target_duration_s'] = float(ordered[-1]['end_s'])
    project['preproduction']['mode'] = 'image_backed'
    project['preproduction']['storyboard_sheet_artifact_ids']=[]
    project['preproduction'].pop('storyboard_split_asset_id',None)
    project['generation_attempts']=generation_attempts
    for asset in registry_rows:
        path=asset.get('path','')
        if (isinstance(path,str) and path.startswith('04_STORYBOARDS/')
                and asset.get('status') in ('available','verified')):
            asset['status']='stale'
    for artifact in project['artifacts']:
        if (artifact.get('type') in ('storyboard_sheet','storyboard_split_manifest')
                and artifact.get('version')==f'v{plan_version}'):
            artifact['status']='stale'
    project['storyboard'].pop('visual_review',None)
    project['storyboard'].pop('document_asset_id',None)
    project.get('review',{}).pop('storyboard_ai_review',None)
    for scene in project['scenes']:
        scene.pop('overview_asset_id',None)
    for artifact in project['artifacts']:
        if artifact.get('type') == 'character_sheet' and artifact.get('version') == f'v{plan_version}':
            artifact['status'] = 'generated'
    return project


def finalize_generation_attempts(project: dict, visual_review: dict) -> dict:
    """Accept only current scene assets after extracted-panel review passes."""
    if (visual_review.get('decision')!='PASS'
            or visual_review.get('source_stage')!='extracted_storyboard_panels'):
        raise ValueError('storyboard_attempt_acceptance_requires_image_only_pass')
    panels=project.get('storyboard',{}).get('panels',[])
    current_assets={panel.get('image_asset_id') for panel in panels
                    if isinstance(panel,dict) and isinstance(panel.get('image_asset_id'),str)}
    current_assets.update(asset_id for shot in project.get('shots',[])
                         for asset_id in shot.get('asset_ids',[]) if isinstance(asset_id,str))
    current_assets.update(character.get('identity_asset_id')
                          for character in project.get('characters',[])
                          if isinstance(character.get('identity_asset_id'),str))
    evidence=[item.removeprefix('sha256:') for item in visual_review.get('evidence_artifact_ids',[])
              if isinstance(item,str) and item.startswith('sha256:')]
    if not evidence:
        raise ValueError('storyboard_review_evidence_missing')
    accepted=evidence[-1]
    registry={row['id']:row for row in project.get('asset_registry',[])
              if isinstance(row,dict) and isinstance(row.get('id'),str)}
    for asset_id in current_assets:
        asset=registry.get(asset_id)
        if (asset and asset.get('kind') in ('scene_image','character_image')
                and asset.get('generation_outcome')=='conditional'):
            asset.update(generation_outcome='accept',review_evidence_sha256=accepted)
    for attempt in project.get('generation_attempts',[]):
        if (attempt.get('result')=='conditional'
                and attempt.get('result_asset_id') in current_assets):
            attempt.update(result='accept',review_evidence_sha256=accepted)
            asset=registry.get(attempt['result_asset_id'])
            if asset is None or asset.get('status') not in ('available','verified'):
                raise ValueError('storyboard_accepted_asset_unavailable')
            asset['generation_outcome']='accept'
    if any(attempt.get('result')=='conditional'
           for attempt in project.get('generation_attempts',[])):
        raise ValueError('storyboard_generation_attempt_unresolved')
    return project


def _storyboard_tools(skill_root: str):
    root = Path(skill_root).expanduser().resolve(strict=True)
    scripts = root / 'contents-production-skills/video-production-assets/scripts'
    if not scripts.is_dir():
        raise ValueError('storyboard_toolchain_missing')
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    from asset_gate import validate_storyboard_extractions
    from render_storyboard_sheet import render_sheets
    from split_storyboard import split_storyboard
    from validate_project import validate
    from validate_storyboard import validate_storyboard
    return validate, validate_storyboard, validate_storyboard_extractions, render_sheets, split_storyboard


def validate_full_storyboard(project: dict, root: Path, skill_root: str) -> None:
    """Fail archive registration unless the actual board and extracted pixels validate."""
    if project.get('creative_plan_schema_version') != '3.0':
        raise ValueError('full_storyboard_schema_required')
    pre=project.get('preproduction') or {}
    review=(project.get('storyboard') or {}).get('visual_review') or {}
    if (pre.get('mode')!='image_backed' or review.get('decision')!='PASS'
            or review.get('source_stage')!='extracted_storyboard_panels'):
        raise ValueError('full_storyboard_visual_review_required')
    validate_project,validate_board,validate_extractions,_,_=_storyboard_tools(skill_root)
    root=Path(root).resolve(strict=True)
    errors=validate_project(project,'plan',str(root))
    errors.extend(validate_board(project,base_dir=root,require_images=True,check_project=False))
    errors.extend(validate_extractions(project,root))
    assets={row.get('id'):row for row in project.get('asset_registry',[])
            if isinstance(row,dict) and isinstance(row.get('id'),str)}
    panels={row.get('shot_id'):row for row in (project.get('storyboard') or {}).get('panels',[])
            if isinstance(row,dict) and isinstance(row.get('shot_id'),str)}
    attempts_by_shot={}
    for attempt in project.get('generation_attempts',[]):
        if isinstance(attempt,dict):
            attempts_by_shot.setdefault(attempt.get('shot_id'),[]).append(attempt)
            prompt=attempt.get('prompt_relative_path')
            expected=attempt.get('prompt_sha256')
            if not isinstance(prompt,str) or not prompt.startswith('06_GENERATION_SPECS/') or not prompt.endswith('.txt'):
                errors.append('storyboard_generation_prompt_path_invalid')
            else:
                try:
                    actual,_=_digest(root,prompt)
                    if actual!=expected:
                        errors.append('storyboard_generation_prompt_hash_mismatch')
                except (OSError,ValueError):
                    errors.append('storyboard_generation_prompt_missing')
            asset=assets.get(attempt.get('result_asset_id'))
            if asset is None or asset.get('generation_outcome')!=attempt.get('result'):
                errors.append('storyboard_generation_asset_outcome_mismatch')
    for shot in project.get('shots',[]):
        shot_id=shot.get('id')
        rows=sorted(attempts_by_shot.get(shot_id,[]),key=lambda row:row.get('attempt',0))
        panel=panels.get(shot_id,{})
        if (not rows or [row.get('attempt') for row in rows]!=list(range(1,len(rows)+1))
                or rows[-1].get('result')!='accept'
                or rows[-1].get('result_asset_id')!=panel.get('image_asset_id')
                or any(row.get('result')!='reject' for row in rows[:-1])):
            errors.append(f'storyboard_generation_history_incomplete:{shot_id}')
        for row in rows:
            if row.get('result')=='reject' and (
                    not row.get('failure_class') or not row.get('observed_failures')
                    or not row.get('review_evidence_sha256')):
                errors.append(f'storyboard_rejected_attempt_evidence_missing:{shot_id}')
            if row.get('result')=='accept' and not row.get('review_evidence_sha256'):
                errors.append(f'storyboard_accepted_attempt_evidence_missing:{shot_id}')
    active_prefix=f"p{project.get('plan_version',1)}-"
    for character in project.get('characters',[]):
        character_id=character.get('id')
        rows=sorted((asset for asset in assets.values()
                     if asset.get('kind')=='character_image'
                     and asset.get('entity_id')==character_id
                     and str(asset.get('version','')).startswith(active_prefix)
                     and type(asset.get('generation_attempt')) is int),
                    key=lambda asset:asset['generation_attempt'])
        current=assets.get(character.get('identity_asset_id'))
        if (not rows or current is not rows[-1]
                or current.get('generation_outcome')!='accept'
                or [row['generation_attempt'] for row in rows]!=list(range(1,len(rows)+1))
                or any(row.get('generation_outcome')!='reject' for row in rows[:-1])):
            errors.append(f'storyboard_character_generation_history_incomplete:{character_id}')
    if errors:
        raise ValueError('full_storyboard_validation_failed:'+';'.join(errors[:30]))


def render_storyboard_artifacts(project: dict, plan: dict, root: Path, skill_root: str,
                                plan_version: int, *, event=None, attempt: int = 1) -> dict:
    """Render ordered <=8-panel sheets, split clean panels, and verify provenance."""
    from .media_runtime import _atomic
    from .contract import canonical_sha256

    root = Path(root).resolve(strict=True)
    validate_project, validate_board, validate_extractions, render_sheets, split_sheets = \
        _storyboard_tools(skill_root)
    version = f'v{plan_version}'
    suffix = f'{version}-a{attempt:02}'
    plan_sha = canonical_sha256(plan)
    render_checkpoint = root / '00_MANIFEST/.media-runtime' / f'storyboard-render-{suffix}.json'
    split_checkpoint = root / '00_MANIFEST/.media-runtime' / f'storyboard-split-{suffix}.json'
    render_checkpoint.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    render_output = f'04_STORYBOARDS/sheets-{suffix}/storyboard.png'
    split_output = f'04_STORYBOARDS/extracted-{suffix}'

    def verify_sheets(rows: list[dict]) -> list[dict]:
        verified = []
        for row in rows:
            relative = row.get('relative_path')
            path = root / relative if isinstance(relative, str) else None
            if (path is None or path.is_symlink() or not path.resolve().is_relative_to(root)
                    or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != row.get('sha256')):
                raise ValueError('storyboard_sheet_checkpoint_mismatch')
            verified.append(row)
        if not verified:
            raise ValueError('storyboard_sheet_generation_empty')
        return verified

    def validate_project_board(require_images: bool) -> None:
        errors = validate_project(project, 'plan', str(root))
        errors.extend(validate_board(project, base_dir=root, require_images=require_images,
                                     check_project=False))
        if errors:
            raise ValueError('storyboard_validation_failed:' + ';'.join(errors[:20]))

    validate_project_board(require_images=True)
    if render_checkpoint.is_file():
        cached = json.loads(render_checkpoint.read_text(encoding='utf-8'))
        if cached.get('plan_sha256') != plan_sha:
            raise ValueError('storyboard_render_plan_conflict')
        rendered = verify_sheets(cached.get('sheets', []))
    else:
        output = root / render_output
        if output.exists() or output.parent.exists():
            raise ValueError('storyboard_render_partial_requires_recovery')
        prompt = ('Deterministically render the current full storyboard to ordered '
                  'PNG sheets, maximum 8 panels per sheet; no image generation.')
        if event:
            event(f'storyboard-sheet-render-{suffix}', prompt, 'started',
                  details={'plan_sha256': plan_sha, 'panels': len(project['storyboard']['panels'])})
        try:
            result = render_sheets(project, root, render_output, panels_per_sheet=8)
            rendered = verify_sheets(result['sheets'])
            _atomic(render_checkpoint, {'plan_sha256': plan_sha, 'sheets': rendered})
            if event:
                event(f'storyboard-sheet-render-{suffix}', prompt, 'completed',
                      outputs=[row['path'] for row in rendered],
                      details={'sheet_count': len(rendered), 'panel_ids': [
                          panel_id for row in rendered for panel_id in row['panel_ids']]})
        except Exception:
            if event:
                event(f'storyboard-sheet-render-{suffix}', prompt, 'failed',
                      details={'plan_sha256': plan_sha})
            raise

    board_artifact_id = project['preproduction']['storyboard_artifact_id']
    sheet_artifact_ids = []
    for index, row in enumerate(rendered, 1):
        asset_id = f'BOARD-SHEET-ASSET-{suffix}-S{index:02}'
        artifact_id = f'BOARD-SHEET-{suffix}-S{index:02}'
        _upsert(project['asset_registry'], {
            'id': asset_id, 'kind': 'storyboard_sheet', 'version': version,
            'status': 'available', 'path': row['relative_path'],
            'sha256': row['sha256'], 'size_bytes': (root/row['relative_path']).stat().st_size,
            'panel_ids': list(row['panel_ids']), 'sheet_index': index,
            'sheet_count': len(rendered), 'source_asset_ids': list(row['source_asset_ids']),
            'source_sha256': dict(row['source_sha256']), 'authority': 'derived',
        })
        _upsert(project['artifacts'], {
            'id': artifact_id, 'type': 'storyboard_sheet', 'version': version,
            'status': 'generated', 'finality': 'preliminary', 'asset_ids': [asset_id],
            'dependencies': [board_artifact_id],
            'dependency_versions': {board_artifact_id: version},
        })
        sheet_artifact_ids.append(artifact_id)
    project['preproduction']['mode'] = 'image_backed'
    project['preproduction']['storyboard_sheet_artifact_ids'] = sheet_artifact_ids
    _atomic(root/'project.json', project)

    if split_checkpoint.is_file():
        cached = json.loads(split_checkpoint.read_text(encoding='utf-8'))
        if cached.get('plan_sha256') != plan_sha or cached.get('sheets') != [
                row['relative_path'] for row in rendered]:
            raise ValueError('storyboard_split_plan_conflict')
        manifest = cached.get('manifest')
    else:
        output = root / split_output
        if output.exists():
            raise ValueError('storyboard_split_partial_requires_recovery')
        prompt = ('Extract every clean panel and one overview per scene from the '
                  'actual rendered sheet pixels; register the derived split manifest.')
        if event:
            event(f'storyboard-sheet-split-{suffix}', prompt, 'started',
                  details={'plan_sha256': plan_sha, 'sheets': [
                      row['relative_path'] for row in rendered]})
        try:
            manifest = split_sheets(
                project, root, output,
                sheets=[row['relative_path'] for row in rendered],
            )
            _atomic(split_checkpoint, {
                'plan_sha256': plan_sha,
                'sheets': [row['relative_path'] for row in rendered],
                'manifest': manifest,
            })
            if event:
                event(f'storyboard-sheet-split-{suffix}', prompt, 'completed',
                      outputs=[str(output/'split-manifest.json'),
                               *[str(output/entry['file']) for entry in manifest['entries']],
                               *[str(output/scene['file']) for scene in manifest['scenes']]],
                      details={'panel_count': len(manifest['entries']),
                               'scene_count': len(manifest['scenes'])})
        except Exception:
            if event:
                event(f'storyboard-sheet-split-{suffix}', prompt, 'failed',
                      details={'plan_sha256': plan_sha})
            raise

    manifest_rel = f'{split_output}/split-manifest.json'
    manifest_path = root / manifest_rel
    manifest_sha = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    split_asset_id = f'STORYBOARD-SPLIT-ASSET-{suffix}'
    split_artifact_id = f'STORYBOARD-SPLIT-{suffix}'
    _upsert(project['asset_registry'], {
        'id': split_asset_id, 'kind': 'storyboard_split_manifest', 'version': version,
        'status': 'available', 'path': manifest_rel, 'sha256': manifest_sha,
        'size_bytes': manifest_path.stat().st_size, 'authority': 'derived',
    })
    _upsert(project['artifacts'], {
        'id': split_artifact_id, 'type': 'storyboard_split_manifest', 'version': version,
        'status': 'generated', 'asset_ids': [split_asset_id],
        'dependencies': list(sheet_artifact_ids),
        'dependency_versions': {artifact_id: version for artifact_id in sheet_artifact_ids},
    })
    project['preproduction']['storyboard_split_asset_id'] = split_asset_id
    panel_paths = {}
    for entry in manifest['entries']:
        relative = f"{split_output}/{entry['file']}"
        path = root / relative
        panel_asset_id = f"BOARD-CUT-{entry['panel_id']}-{suffix}"
        _upsert(project['asset_registry'], {
            'id': panel_asset_id, 'kind': 'image', 'version': version,
            'status': 'available', 'path': relative, 'sha256': entry['sha256'],
            'size_bytes': path.stat().st_size,
            'source_asset_ids': [entry['source_asset_id']],
            'authority': 'derived',
        })
        panel = next(row for row in project['storyboard']['panels']
                     if row['id'] == entry['panel_id'])
        panel['extracted_asset_id'] = panel_asset_id
        panel_paths[entry['shot_id']] = str(path)
    for scene in manifest['scenes']:
        relative = f"{split_output}/{scene['file']}"
        path = root / relative
        overview_id = f"BOARD-OVERVIEW-{scene['scene_id']}-{suffix}"
        _upsert(project['asset_registry'], {
            'id': overview_id, 'kind': 'image', 'version': version,
            'status': 'available', 'path': relative, 'sha256': scene['sha256'],
            'size_bytes': path.stat().st_size, 'authority': 'derived',
        })
        scene_row = next(row for row in project['scenes'] if row['id'] == scene['scene_id'])
        scene_row['overview_asset_id'] = overview_id

    validate_project_board(require_images=True)
    extraction_errors = validate_extractions(project, root)
    if extraction_errors:
        raise ValueError('storyboard_extraction_validation_failed:'+';'.join(extraction_errors[:20]))
    _atomic(root/'project.json', project)
    return {
        'sheets': rendered,
        'split_manifest_asset_id': split_asset_id,
        'split_manifest_sha256': manifest_sha,
        'panel_count': len(manifest['entries']),
        'scene_count': len(manifest['scenes']),
        'panel_paths': panel_paths,
        'human_review': 'NOT_PERFORMED',
    }
