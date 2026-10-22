"""Select editable production outputs, never whole runtime/history directories."""
import hashlib
import json
from pathlib import Path
import re
from .media_runtime import _atomic

PROFILE='deliverables-v2'
from worker.adapters.project_bundle_contract import (
    DELIVERABLE_CORE as CORE,DELIVERABLE_DOCUMENTS as DOCUMENTS,
    DELIVERABLE_OPTIONAL as OPTIONAL,storyboard_deliverable_paths)


def checked_path(root,name):
    relative=Path(name)
    if relative.is_absolute() or '..' in relative.parts or '\\' in name:raise ValueError('unsafe_deliverable_path')
    path=root/relative
    if any(p.is_symlink() for p in (path,*path.parents)) or not path.resolve().is_relative_to(root):
        raise ValueError('unsafe_deliverable_path')
    return path


def asset_paths(specs):
    found=set();ids=set()
    for spec in specs:
        aid=spec['asset_id'];name=spec['relative_path']
        if aid in ids:raise ValueError('duplicate_deliverable_asset')
        if (spec['kind'] not in ('character_image','scene_image')
                or (spec['kind']=='character_image')!=aid.startswith('CH')):
            raise ValueError('unexpected_deliverable_asset')
        ids.add(aid)
        pattern=(rf'02_MASTER_ASSETS/CHARACTERS/{aid}-identity-(?:v[12]|v1-v2)\.png' if spec['kind']=='character_image'
                 else rf'03_SCENE_PACKS/{aid}(?:-v[12])?(?:-lettered(?:-[0-9a-f]{{8}})?)?(?:-v[12])?\.png')
        if not re.fullmatch(r'(CH|SH)[0-9]{2}',aid) or not re.fullmatch(pattern,name):raise ValueError('unexpected_deliverable_asset')
        found.add(name)
    return found


def _md_cell(value):
    if isinstance(value,(dict,list)):
        value=json.dumps(value,ensure_ascii=False,sort_keys=True)
    return str(value if value is not None else 'N/A').replace('|','\\|').replace('\n',' ').strip()


def _full_storyboard_document(root,project,plan,specs,result):
    pre=project.get('preproduction') or {}
    board=project.get('storyboard') or {}
    review=board.get('visual_review') or {}
    if (pre.get('mode')!='image_backed' or review.get('decision')!='PASS'
            or review.get('source_stage')!='extracted_storyboard_panels'):
        raise RuntimeError('full_storyboard_review_required_before_archive')
    assets={row['id']:row for row in project.get('asset_registry',[])
            if isinstance(row,dict) and isinstance(row.get('id'),str)}
    artifacts={row['id']:row for row in project.get('artifacts',[])
                if isinstance(row,dict) and isinstance(row.get('id'),str)}
    shots={row['id']:row for row in project.get('shots',[])
           if isinstance(row,dict) and isinstance(row.get('id'),str)}
    spec_by_id={row['asset_id']:row for row in specs}
    panels=board.get('panels',[])
    beats=board.get('beats',[])
    by_beat={row['id']:row for row in beats}
    by_panel={row['id']:row for row in panels}
    scene_asset_count=len({row.get('image_asset_id') for row in panels
                           if isinstance(row.get('image_asset_id'),str)})
    character_asset_count=len({row.get('identity_asset_id') for row in project.get('characters',[])
                               if isinstance(row.get('identity_asset_id'),str)})
    active_attempt_prefix=f"p{project.get('plan_version',1)}-"
    image_attempt_count=sum(
        1 for row in assets.values()
        if row.get('kind') in ('scene_image','character_image')
        and str(row.get('version','')).startswith(active_attempt_prefix)
        and type(row.get('generation_attempt')) is int)
    lines=[
        '# Full production storyboard — '+plan['title'],
        '',
        f"- Board purpose: {_md_cell(board.get('purpose'))}",
        f"- Recipient / stage: {_md_cell(board.get('recipient'))} / {_md_cell(board.get('usage_stage'))}",
        f"- Plan version: {_md_cell(project.get('version'))}; schema: {_md_cell(plan.get('schema_version'))}",
        f"- Runtime: {project.get('target_duration_s')} seconds at {project.get('fps')} fps; status: {_md_cell(pre.get('mode'))}.",
        f"- AI image-only review: {_md_cell(review.get('decision'))}; human review: {_md_cell(review.get('human_review'))}.",
        f"- Rights state: {_md_cell((result.get('qa_checks') or [{}])[-1].get('status'))}; YouTube publication authorization: false.",
        '',
        '## 1. Inputs, approvals, fixed story, and scope',
        '',
        f"- Synopsis: {_md_cell(plan.get('synopsis'))}",
        f"- Selected direction: {_md_cell(plan.get('logline'))}",
        f"- Narrative master SHA-256: `{hashlib.sha256(plan['narrative']['master_text'].encode('utf-8')).hexdigest()}`.",
        f"- Production approval plan SHA-256: `{_md_cell((project.get('review') or {}).get('plan_sha256','recorded in project ledger'))}`.",
        "- Human listening, human image acceptance, and solved 3D feasibility are not claimed.",
        '',
        '## 2. Complete beat → scene → shot → panel coverage and scene-camera intent',
        '',
        '| Beat / synopsis locator | Event and emotion | Scenes | Shots | Panels |',
        '|---|---|---|---|---|',
    ]
    for beat in beats:
        shot_rows=[row for row in project['shots'] if beat['id'] in row.get('beat_ids',[])]
        scene_ids=list(dict.fromkeys(row['scene_id'] for row in shot_rows))
        panel_ids=[panel['id'] for panel in panels if beat['id'] in panel.get('beat_ids',[])]
        lines.append('| '+' | '.join(_md_cell(v) for v in (
            beat['id']+' / '+beat['synopsis_locator'],beat['event']+' / '+beat['emotion'],
            ', '.join(scene_ids),', '.join(row['id'] for row in shot_rows),
            ', '.join(panel_ids)))+' |')
    lines.extend([
        '',
        '| Scene / shots | Purpose | Entry → exit knowledge/emotion | POV / axis | Camera grammar and movement reason | Neighbor/reuse comparison |',
        '|---|---|---|---|---|---|',
    ])
    for scene in project['scenes']:
        intent=scene['camera_intent']
        lines.append('| '+' | '.join(_md_cell(v) for v in (
            scene['id']+' / '+', '.join(intent['shot_ids']),intent['purpose'],
            intent['entry_knowledge']+' / '+intent['exit_knowledge']+'; '+intent['entry_emotion']+' → '+intent['exit_emotion'],
            intent['pov_axis']+'; '+intent['camera_grammar']+'; '+intent['movement_reason'],
            intent['neighboring_relation']+'; '+intent['reuse_reason']))+' |')
    lines.extend([
        '',
        '## 3. Complete shot specifications',
        '',
        '| Shot / source locator | Beat / scene | Actual range / source state | Purpose and subject state | Camera | Spatial / VFX / contact | Speech / audio |',
        '|---|---|---|---|---|---|---|',
    ])
    for shot in sorted(project['shots'],key=lambda row:row['start_s']):
        speech='; '.join(
            f"{line['id']} {line['start_s']:.3f}-{line['end_s']:.3f}s {line['text']}"
            for line in shot.get('speech',[]))
        camera=shot['camera']
        lines.append('| '+' | '.join(_md_cell(v) for v in (
            shot['id']+' / '+shot['synopsis_locator'],
            ', '.join(shot['beat_ids'])+' / '+shot['scene_id'],
            f"{shot['start_s']:.3f}-{shot['end_s']:.3f}s ({shot['source_duration_status']})",
            shot['purpose']+'; '+shot['start_state']+' → '+shot['end_state'],
            f"{camera['shot_size']}; {camera['angle']}; {camera['framing']}; {camera['movement']} ({camera['reason']}); {camera['start']} → {camera['end']}",
            shot['spatial']['mode']+': '+shot['spatial']['reason']+'; VFX '+str(shot['vfx']['enabled'])+': '+shot['vfx']['description'],
            speech+'; audio cues: '+', '.join(shot['audio_cue_ids'])))+' |')
    lines.extend([
        '',
        '## 4. Complete panel table and clean image references',
        '',
        '| Panel / frame | Shot / scene / beats | Visible characters / audio / speech | One drawable action | Reveals / withholds / continuity | Source / extracted clean cut | Reference assets / versions | Submitted prompt / SHA-256 |',
        '|---|---|---|---|---|---|---|---|',
    ])
    for panel in panels:
        source=assets[panel['image_asset_id']]
        extracted=assets[panel['extracted_asset_id']]
        links=(f"[{source['id']}](../{source['path']}) / "
               f"[clean cut](../{extracted['path']})")
        spec=spec_by_id[panel['shot_id']]
        prompt_path=spec.get('prompt_relative_path',
                            f"06_GENERATION_SPECS/{panel['shot_id']}-prompt-v1.txt")
        prompt_bytes=checked_path(root,prompt_path).read_bytes()
        prompt_link=(f"[submitted prompt](../{prompt_path}) / "
                     f"sha256:{hashlib.sha256(prompt_bytes).hexdigest()}")
        references=', '.join(
            f"{asset_id}@{version}" for asset_id,version in
            sorted(panel.get('asset_version_refs',{}).items()))
        lines.append('| '+' | '.join(_md_cell(v) for v in (
            panel['id']+f" / {panel['role']} @ {panel['frame_time_s']:.3f}s",
            panel['shot_id']+' / '+shots[panel['shot_id']]['scene_id']+' / '+', '.join(panel['beat_ids']),
            ', '.join(panel['visible_character_ids'])+' / '+', '.join(panel['audio_cue_ids'])+' / '+', '.join(panel['speech_ids']),
            panel['visual_action'],panel['reveals']+' / '+panel['withholds']+' / '+panel['continuity'],
            links,references,prompt_link))+' |')
    lines.extend([
        '',
        '## 5. Image counts, ordered sheets, extraction, and file handoff',
        '',
        f"- Canonical panels: {len(panels)}; current unique scene source images: {scene_asset_count}; current character identity sheets: {character_asset_count}; registered image attempts: {image_attempt_count}.",
        f"- Registered storyboard sheets: {len(pre.get('storyboard_sheet_artifact_ids',[]))}; extracted clean panels: {len(json.loads(checked_path(root,assets[pre['storyboard_split_asset_id']]['path']).read_text(encoding='utf-8'))['entries'])}; scene overviews: {len(json.loads(checked_path(root,assets[pre['storyboard_split_asset_id']]['path']).read_text(encoding='utf-8'))['scenes'])}.",
        f"- Split manifest: `{assets[pre['storyboard_split_asset_id']]['path']}`; SHA-256 `{assets[pre['storyboard_split_asset_id']]['sha256']}`.",
        '',
        '| Sheet artifact | Panel IDs in order | File / SHA-256 |',
        '|---|---|---|',
    ])
    for artifact_id in pre['storyboard_sheet_artifact_ids']:
        artifact=artifacts[artifact_id]
        sheet=assets[artifact['asset_ids'][0]]
        lines.append('| '+' | '.join(_md_cell(v) for v in (
            artifact_id,', '.join(sheet['panel_ids']),sheet['path']+' / '+sheet['sha256']))+' |')
    lines.extend([
        '',
        '## 6. Image-only review, KEEP/FIX status, and unverified checks',
        '',
        '| Location / IDs | Status | Observed finding / evidence | Minimal correction | Preserve | Owner | Dependencies | Time / basis / recheck |',
        '|---|---|---|---|---|---|---|---|',
    ])
    readings={row['shot_id']:row for row in review.get('image_only_reading',{}).get('readings',[])
              if isinstance(row,dict) and isinstance(row.get('shot_id'),str)}
    checks={row['shot_id']:row for row in review.get('emotion_comparison',{}).get('checks',[])
            if isinstance(row,dict) and isinstance(row.get('shot_id'),str)}
    attempt_rows=[]
    for attempt in project.get('generation_attempts',[]):
        asset=assets.get(attempt.get('result_asset_id'),{})
        if asset.get('kind')!='scene_image':
            continue
        attempt_rows.append({
            'kind':'scene','entity_id':attempt.get('shot_id'),'attempt':attempt.get('attempt'),
            'asset_id':asset.get('id'),'path':asset.get('path'),
            'prompt_relative_path':attempt.get('prompt_relative_path'),
            'result':attempt.get('result'),'observed_failures':attempt.get('observed_failures',[]),
            'changes':attempt.get('changes',[]),'preserve':attempt.get('preserve',[]),
            'source_asset_ids':attempt.get('source_asset_ids',[]),
            'review_evidence_sha256':attempt.get('review_evidence_sha256'),
        })
    for asset in assets.values():
        if (asset.get('kind')!='character_image'
                or not str(asset.get('version','')).startswith(active_attempt_prefix)
                or type(asset.get('generation_attempt')) is not int):
            continue
        attempt_rows.append({
            'kind':'character','entity_id':asset.get('entity_id'),
            'attempt':asset['generation_attempt'],'asset_id':asset.get('id'),
            'path':asset.get('path'),'prompt_relative_path':asset.get('prompt_relative_path'),
            'result':asset.get('generation_outcome'),
            'observed_failures':asset.get('observed_failures',[]),
            'changes':asset.get('changes',[]),'preserve':asset.get('preserve',[]),
            'source_asset_ids':asset.get('source_asset_ids',[]),
            'review_evidence_sha256':asset.get('review_evidence_sha256'),
        })
    attempt_rows.sort(key=lambda row:(row['kind'],row.get('entity_id') or '',row.get('attempt') or 0))
    following={(row['kind'],row['entity_id'],row['attempt']):row for row in attempt_rows}
    character_names={row['id']:row.get('name','') for row in project.get('characters',[])}
    for item in attempt_rows:
        kind,entity,number=item['kind'],item.get('entity_id'),item.get('attempt')
        key=(kind,entity,number+1) if isinstance(number,int) else None
        next_attempt=following.get(key) if key else None
        source=assets.get(item.get('asset_id'),{})
        image_path=item.get('path')
        image_link=f"[{item.get('asset_id')}](../{image_path})" if image_path else 'N/A'
        prompt_path=item.get('prompt_relative_path')
        prompt_link=(f"[prompt](../{prompt_path})" if isinstance(prompt_path,str) else 'N/A')
        if kind=='scene':
            shot=shots.get(entity,{})
            location=f"{entity} / {shot.get('scene_id','N/A')} / "+', '.join(
                panel['id'] for panel in panels if panel.get('shot_id')==entity)
            reading=readings.get(entity,{})
            check=checks.get(entity,{})
            visible='; '.join(value for value in (
                reading.get('visible_action'),reading.get('visible_emotion'),check.get('evidence'))
                if isinstance(value,str) and value)
        else:
            location=f"{entity} / {character_names.get(entity,'character identity sheet')}"
            visible=''
        location+=f" / attempt {number} / {image_link} / {prompt_link}"
        findings=item.get('observed_failures') or []
        if findings:
            visible='; '.join([*findings,*([visible] if visible else [])])
        elif not visible:
            visible='No unresolved visual finding recorded for this attempt.'
        result=item.get('result')
        status=('FIX — rejected attempt' if result=='reject' else
                'KEEP — AI image-only PASS' if result=='accept' else 'UNVERIFIED')
        corrections=item.get('changes') or (next_attempt or {}).get('changes') or []
        correction='; '.join(corrections) if corrections else 'No blocking correction recorded.'
        preserve='; '.join(item.get('preserve') or []) or 'Approved story, cast identity, and shot order.'
        dependencies=[]
        for source_id in item.get('source_asset_ids') or []:
            dependency=assets.get(source_id,{})
            dependencies.append(f"{source_id}@{dependency.get('version','unknown')}")
        owner='AI visual reviewer; human review NOT_PERFORMED'
        if next_attempt and next_attempt.get('result')=='accept':
            recheck=f"PASS in attempt {next_attempt['attempt']}"
        elif result=='accept':
            recheck='PASS'+(f"; evidence sha256:{item['review_evidence_sha256']}"
                            if item.get('review_evidence_sha256') else '')
        else:
            recheck='UNVERIFIED'
        timing='Not measured; reviewer latency not retained'
        lines.append('| '+' | '.join(_md_cell(v) for v in (
            location,status,visible,correction,preserve,owner,', '.join(dependencies),
            timing+'; '+recheck))+' |')
    lines.append('| '+' | '.join(_md_cell(v) for v in (
        'Whole-video listening and human acceptance','UNVERIFIED',
        'No human listening or approval evidence was supplied.',
        'No automatic approval is inferred.','Accepted storyboard assets remain subject to human review.',
        'Human operator','Narration, mix, and final render','Not measured; NOT_PERFORMED'))+' |')
    lines.append('| '+' | '.join(_md_cell(v) for v in (
        '3D spatial feasibility','N/A — reasoned 2D still pipeline',
        'Per-shot spatial fields explain why numerical 3D verification is not applicable.',
        'Contact readability remains checked in the actual 2D panels.',
        '2D spatial continuity is preserved; no 3D claim is made.',
        'Storyboard + AI visual QA','Rendered 2D panels','Not measured; N/A, no 3D claim'))+' |')
    lines.extend([
        '',
        f"Review evidence: `{', '.join(review.get('evidence_artifact_ids',[]))}`. The board remains `{_md_cell(artifacts[pre['storyboard_artifact_id']].get('finality'))}`; this is not a human acceptance or a verified 3D package.",
        '',
    ])
    return '\n'.join(lines)


def write_documents(root,plan,specs,result):
    root=Path(root).resolve(strict=True)
    segments={s['id']:s['text'] for s in plan['narration_segments']}
    selected={s['asset_id']:s for s in specs}
    emotional=plan.get('schema_version') in ('2.0','3.0')
    spoken=plan['narrative']['master_text'] if emotional else '\n\n'.join(segments.values())
    script='# '+plan['title']+'\n\n'+spoken+'\n'
    if plan.get('schema_version')=='3.0':
        project=json.loads(checked_path(root,'project.json').read_text(encoding='utf-8'))
        board=_full_storyboard_document(root,project,plan,specs,result)
    else:
        board=['# 스토리보드 — '+plan['title'],
               '\n예상 길이와 실제 음성 길이는 다를 수 있습니다. 실제 영상: '+str(result['duration_seconds'])+'초.\n']
        for shot in plan['shots']:
            image=selected[shot['id']]['relative_path']
            board.extend(['## '+shot['id'],shot.get('action',''),segments.get(shot.get('segment_id'),''),
                          '!['+shot['id']+'](../'+image+')',''])
            if emotional:
                board.extend(['Stage: '+shot['stage'], '```json\n'+json.dumps(shot['performance'],ensure_ascii=False,indent=2)+'\n```'])
    characters=['# 캐릭터 바이블']
    for c in plan['characters']:
        characters.extend(['## '+c['id']+' — '+c.get('name',''),
            '!['+c['id']+'](../'+selected[c['id']]['relative_path']+')',
            '```json\n'+json.dumps(c,ensure_ascii=False,indent=2)+'\n```',''])
    prompts=['# 최종 선택 이미지의 생성 프롬프트']
    for spec in specs:
        name=spec.get('prompt_relative_path','06_GENERATION_SPECS/'+spec['asset_id']+'-prompt-v1.txt')
        prompt=checked_path(root,name).read_text()
        prompts.extend(['## '+spec['asset_id'],prompt,''])
        if spec.get('lettering'):prompts.extend(['Deterministic lettering/edit recipe:',json.dumps(spec['lettering'],ensure_ascii=False,indent=2),''])
    board_text=board if isinstance(board,str) else '\n\n'.join(board)
    for name,text in {'02_STORY/script.md':script,'02_STORY/storyboard.md':board_text,
                      '04_CHARACTER_BIBLE/characters.md':'\n\n'.join(characters),
                      '06_GENERATION_SPECS/selected-prompts.md':'\n\n'.join(prompts)}.items():
        path=checked_path(root,name);path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        path.write_text(text,encoding='utf-8');path.chmod(0o600)
    _atomic(checked_path(root,'09_QA/final-qa-summary.json'),{
        'story_id':root.name,'render_job_id':result['render_job_id'],'sha256':result['sha256'],
        'duration_seconds':result['duration_seconds'],'qa_checks':result['qa_checks'],
        'review_scope':'machine checks; not human review; raw execution logs excluded',
        **({k:result[k] for k in ('narrative_review','narrative_visual_review')} if emotional else {})})
    if plan.get('schema_version')=='3.0':
        project_path=checked_path(root,'project.json')
        project=json.loads(project_path.read_text(encoding='utf-8'))
        version=project['plan_version'];relative='02_STORY/storyboard.md';path=checked_path(root,relative)
        raw=path.read_bytes();asset_id=f'STORYBOARD-DOCUMENT-v{version}'
        board_doc_asset={
            'id':asset_id,'kind':'storyboard_document','version':f'v{version}',
            'status':'available','path':relative,'sha256':hashlib.sha256(raw).hexdigest(),
            'size_bytes':len(raw),'authority':'derived',
        }
        registry=project['asset_registry']
        existing=next((index for index,row in enumerate(registry)
                       if row.get('id')==asset_id),None)
        if existing is None:registry.append(board_doc_asset)
        else:registry[existing]=board_doc_asset
        board_id=project['preproduction']['storyboard_artifact_id']
        artifact=next(row for row in project['artifacts'] if row['id']==board_id)
        artifact['asset_ids']=list(dict.fromkeys([*artifact.get('asset_ids',[]),asset_id]))
        project['storyboard']['document_asset_id']=asset_id
        _atomic(project_path,project)


def collect(root,story_id,specs):
    root=Path(root).resolve(strict=True)
    from worker.adapters.project_bundle_contract import versioned_core,versioned_optional
    project=json.loads((root/'project.json').read_text())
    version=project.get('plan_version',1)
    names=versioned_core(version)|DOCUMENTS|asset_paths(specs)|{'10_DELIVERY/'+story_id+'-v1.mp4'}
    names|={spec.get('prompt_relative_path',
                    f"06_GENERATION_SPECS/{spec['asset_id']}-prompt-v1.txt")
            for spec in specs}
    names|={name for name in versioned_optional(version) if checked_path(root,name).is_file()}
    if project.get('creative_plan_schema_version')=='3.0':
        pre=project.get('preproduction') or {}
        visual_review=(project.get('storyboard') or {}).get('visual_review') or {}
        if (pre.get('mode')!='image_backed' or visual_review.get('decision')!='PASS'
                or visual_review.get('source_stage')!='extracted_storyboard_panels'):
            raise RuntimeError('full_storyboard_archive_gate_failed')
        assets={row['id']:row for row in project.get('asset_registry',[])
                if isinstance(row,dict) and isinstance(row.get('id'),str)}
        split=assets.get(pre.get('storyboard_split_asset_id'))
        if not isinstance(split,dict):
            raise RuntimeError('storyboard_split_manifest_missing')
        split_path=checked_path(root,split['path'])
        manifest=json.loads(split_path.read_text(encoding='utf-8'))
        names|=storyboard_deliverable_paths(project,manifest)
    files=[]
    for name in sorted(names):
        path=checked_path(root,name)
        if not path.is_file():raise RuntimeError('required_deliverable_missing:'+name)
        files.append((name,path))
    return files
