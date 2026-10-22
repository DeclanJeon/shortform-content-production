"""Shared, dependency-free archive path allowlist."""
from pathlib import PurePosixPath
MAX_ZIP=256*1024*1024
TOP={'00_MANIFEST','01_SOURCE','02_STORY','02_MASTER_ASSETS','03_SCENE_PACKS','04_CHARACTER_BIBLE','04_STORYBOARDS',
     '06_GENERATION_SPECS','07_AUDIO','08_ANIMATIC_EDIT','09_QA','10_DELIVERY','.history'}
EXT={'.json','.png','.wav','.srt','.ass','.txt','.md','.mp4'}


DELIVERABLE_CORE={'project.json','00_MANIFEST/creative-plan-v1.json',
    '00_MANIFEST/asset-specs-selected-v1.json','01_SOURCE/source-snapshot.json',
    '09_QA/production-approval.json','10_DELIVERY/automatic-media-result-v1.json'}
DELIVERABLE_DOCUMENTS={'02_STORY/script.md','02_STORY/storyboard.md','04_CHARACTER_BIBLE/characters.md',
    '06_GENERATION_SPECS/selected-prompts.md','09_QA/final-qa-summary.json'}
DELIVERABLE_OPTIONAL={'07_AUDIO/narration-v1.txt','01_SOURCE/originality-assessment-v1.json',
    '07_AUDIO/bgm-license.json','07_AUDIO/bgm-source.wav','08_ANIMATIC_EDIT/listening-edit.json',
    '08_ANIMATIC_EDIT/template-source.png'}


def versioned_core(version=1):
    if type(version) is not int or not 1<=version<=20:raise ValueError('invalid_bundle_plan_version')
    return (DELIVERABLE_CORE-{'00_MANIFEST/creative-plan-v1.json'})|{f'00_MANIFEST/creative-plan-v{version}.json'}


def versioned_optional(version=1):
    versioned_core(version)
    return DELIVERABLE_OPTIONAL|{f'00_MANIFEST/creative-plan-v{i}.json' for i in range(1,version)}|{
        f'{folder}/{stem}-v{i}.{ext}' for i in range(1,version+1)
        for folder,stem,ext in [('07_AUDIO','narration','txt'),('01_SOURCE','originality-assessment','json')]}


def storyboard_deliverable_paths(project, split_manifest=None):
    """Return current image-backed board files, rejecting incomplete references."""
    if not isinstance(project,dict):
        raise ValueError('invalid_storyboard_project')
    preproduction=project.get('preproduction')
    if not isinstance(preproduction,dict) or preproduction.get('mode')!='image_backed':
        return set()
    assets={row.get('id'):row for row in project.get('asset_registry',[])
            if isinstance(row,dict) and isinstance(row.get('id'),str)}
    artifacts={row.get('id'):row for row in project.get('artifacts',[])
               if isinstance(row,dict) and isinstance(row.get('id'),str)}

    def path_for(asset_id,kind):
        asset=assets.get(asset_id)
        path=asset.get('path') if isinstance(asset,dict) else None
        if (not isinstance(path,str) or not allowed(path) or asset.get('kind')!=kind
                or asset.get('status') not in ('available','verified')):
            raise ValueError('storyboard_deliverable_asset_missing')
        return path

    sheet_ids=preproduction.get('storyboard_sheet_artifact_ids')
    if (not isinstance(sheet_ids,list) or not sheet_ids
            or any(not isinstance(aid,str) for aid in sheet_ids)
            or len(set(sheet_ids))!=len(sheet_ids)):
        raise ValueError('storyboard_sheet_artifacts_missing')
    paths=set()
    ordered_sheet_paths=[]
    for artifact_id in sheet_ids:
        artifact=artifacts.get(artifact_id)
        asset_ids=artifact.get('asset_ids') if isinstance(artifact,dict) else None
        if (not isinstance(artifact,dict) or artifact.get('type')!='storyboard_sheet'
                or not isinstance(asset_ids,list) or len(asset_ids)!=1):
            raise ValueError('storyboard_sheet_artifact_invalid')
        relative=path_for(asset_ids[0],'storyboard_sheet')
        paths.add(relative)
        ordered_sheet_paths.append(relative)
    split_id=preproduction.get('storyboard_split_asset_id')
    split_path=path_for(split_id,'storyboard_split_manifest')
    paths.add(split_path)
    if not isinstance(split_manifest,dict) or (
            split_manifest.get('kind')!='split-manifest'
            or split_manifest.get('derived') is not True
            or split_manifest.get('mode')!='sheet'
            or split_manifest.get('project_id')!=project.get('project_id')):
        raise ValueError('storyboard_split_manifest_invalid')
    root=PurePosixPath(split_path).parent
    manifest_sheets=split_manifest.get('sheets')
    if (not isinstance(manifest_sheets,list)
            or [row.get('path') for row in manifest_sheets if isinstance(row,dict)]!=ordered_sheet_paths):
        raise ValueError('storyboard_split_sheet_coverage_invalid')
    for collection in ('entries','scenes'):
        rows=split_manifest.get(collection)
        if not isinstance(rows,list) or not rows:
            raise ValueError('storyboard_split_outputs_missing')
        for row in rows:
            relative=row.get('file') if isinstance(row,dict) else None
            if not isinstance(relative,str) or not allowed((root/PurePosixPath(relative)).as_posix()):
                raise ValueError('storyboard_split_output_path_invalid')
            paths.add((root/PurePosixPath(relative)).as_posix())
    version=f"v{project.get('plan_version',1)}"
    for asset in assets.values():
        if (asset.get('kind') in ('synopsis_document','style_world_bible','character_ssot')
                and asset.get('version')==version
                and asset.get('status') in ('available','verified')):
            relative=asset.get('path')
            if not isinstance(relative,str) or not allowed(relative):
                raise ValueError('storyboard_document_path_invalid')
            paths.add(relative)
    active_attempt_prefix=f"p{project.get('plan_version',1)}-"
    pending=[asset.get('id') for asset in assets.values()
             if asset.get('kind') in ('scene_image','character_image')
             and str(asset.get('version','')).startswith(active_attempt_prefix)
             and asset.get('generation_outcome') in ('accept','reject','conditional')]
    visited=set()
    while pending:
        asset_id=pending.pop()
        if asset_id in visited:
            continue
        visited.add(asset_id)
        asset=assets.get(asset_id)
        if (not isinstance(asset,dict) or asset.get('status') not in ('available','verified')
                or not isinstance(asset.get('path'),str) or not allowed(asset['path'])):
            raise ValueError('storyboard_attempt_asset_missing')
        paths.add(asset['path'])
        prompt=asset.get('prompt_relative_path')
        if prompt is not None:
            if not isinstance(prompt,str) or not allowed(prompt):
                raise ValueError('storyboard_attempt_prompt_path_invalid')
            paths.add(prompt)
        source_ids=asset.get('source_asset_ids',[])
        if not isinstance(source_ids,list):
            raise ValueError('storyboard_attempt_sources_invalid')
        master=asset.get('master_asset_ref')
        if isinstance(master,dict) and isinstance(master.get('asset_id'),str):
            source_ids=[*source_ids,master['asset_id']]
        pending.extend(source_id for source_id in source_ids if isinstance(source_id,str))
        if any(not isinstance(source_id,str) for source_id in source_ids):
            raise ValueError('storyboard_attempt_sources_invalid')
    for attempt in project.get('generation_attempts',[]):
        prompt=attempt.get('prompt_relative_path') if isinstance(attempt,dict) else None
        if prompt is not None:
            if not isinstance(prompt,str) or not allowed(prompt):
                raise ValueError('storyboard_attempt_prompt_path_invalid')
            paths.add(prompt)
    return paths


def allowed(name):
    p=PurePosixPath(name)
    return (not p.is_absolute() and '..' not in p.parts and '\\' not in name
            and (name=='project.json' or (p.parts and p.parts[0] in TOP)) and p.suffix.lower() in EXT
            and not any(x in p.parts for x in ('private','.pi','.creative-runtime','.media-runtime','.policy-runtime','.archive-runtime','secrets'))
            and not any(x in p.name.lower() for x in ('auth.json','credential','secret','token','.env')))
