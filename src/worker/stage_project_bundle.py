"""Content-checked project ZIP staging via trusted SSH; no extraction or secrets."""
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import uuid
import zipfile
import psycopg
from psycopg.rows import dict_row

from adapters.project_bundle_contract import (
    MAX_ZIP,allowed,DELIVERABLE_CORE,DELIVERABLE_DOCUMENTS,DELIVERABLE_OPTIONAL,
    versioned_core,versioned_optional,storyboard_deliverable_paths,
)


def digest(path):
    sha=hashlib.sha256();md5=hashlib.md5();size=0
    with Path(path).open('rb') as f:
        while chunk:=f.read(1024*1024):sha.update(chunk);md5.update(chunk);size+=len(chunk)
    return sha.hexdigest(),md5.hexdigest(),size


def zip_entry(out,name,data):
    info=zipfile.ZipInfo(name,date_time=(2026,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED
    info.external_attr=0o100600<<16;out.writestr(info,data,compress_type=zipfile.ZIP_DEFLATED,compresslevel=6)


def stage(packet,root,jobs):
    expected={'story_id','render_job_id','tts_job_id','zip_sha256','zip_base64'}
    revision=packet.get('bundle_revision',1)
    replacement=packet.get('replacement_of')
    if revision==2:
        expected|={'bundle_revision','replacement_of'}
        if type(replacement) is not int or replacement<=0:raise ValueError('bundle_replacement_identity')
    elif revision!=1:raise ValueError('bundle_revision_invalid')
    if set(packet)!=expected or not re.fullmatch(r'[0-9]{8}-auto-[0-9a-f]{12}',packet['story_id']):raise ValueError('invalid_bundle_identity')
    rid=str(uuid.UUID(packet['render_job_id']));tid=str(uuid.UUID(packet['tts_job_id']))
    raw=base64.b64decode(packet['zip_base64'],validate=True)
    if not 0<len(raw)<=MAX_ZIP or hashlib.sha256(raw).hexdigest()!=packet['zip_sha256']:raise ValueError('bundle_input_checksum')
    root=Path(root).resolve(strict=True)
    for row,stage_name in zip(jobs,('render','tts')):
        if row['story_id']!=packet['story_id'] or row['stage']!=stage_name or row['status']!='succeeded':raise ValueError('bundle_job_lineage')
    if str(jobs[0]['run_id'])!=str(jobs[1]['run_id']):raise ValueError('bundle_run_lineage')
    folder=root/'project-bundles'/packet['story_id'];folder.mkdir(parents=True,exist_ok=True,mode=0o700)
    if not folder.resolve().is_relative_to(root):raise ValueError('bundle_path_escape')
    stem=rid+('-deliverables-v2' if revision==2 else '')
    final=folder/(stem+'.zip');receipt=folder/(stem+'.json')
    if final.exists() or receipt.exists():
        old=json.loads(receipt.read_text())
        if old['input_sha256']!=packet['zip_sha256'] or digest(final)!=(old['sha256'],old['md5'],old['size_bytes']):raise ValueError('bundle_cache_conflict')
        return old
    source=zipfile.ZipFile(io.BytesIO(raw));infos=source.infolist()
    if len(infos)>1000 or len({i.filename for i in infos})!=len(infos) or sum(i.file_size for i in infos)>512*1024*1024:raise ValueError('bundle_limits')
    if source.getinfo('BUNDLE_MANIFEST.json').file_size>1024*1024:raise ValueError('bundle_manifest_limit')
    manifest=json.loads(source.read('BUNDLE_MANIFEST.json'))
    if any(manifest.get(k)!=packet[k] for k in ('story_id','render_job_id','tts_job_id')):raise ValueError('bundle_manifest_lineage')
    entries={x['path']:x for x in manifest['entries']};contents={}
    for info in infos:
        if info.filename=='BUNDLE_MANIFEST.json':continue
        if not allowed(info.filename) or info.file_size>128*1024*1024 or info.flag_bits&1 or stat.S_ISLNK(info.external_attr>>16):raise ValueError('unsafe_bundle_entry')
        data=source.read(info)
        if info.filename not in entries or entries[info.filename]['sha256']!=hashlib.sha256(data).hexdigest() or entries[info.filename]['size_bytes']!=len(data):raise ValueError('bundle_entry_checksum')
        contents[info.filename]=data
    if set(contents)!=set(entries):raise ValueError('bundle_manifest_incomplete')
    version=manifest.get('plan_version',1)
    required=versioned_core(version)
    if not required<=set(contents):raise ValueError('bundle_required_provenance_missing')
    lean=manifest.get('retention_profile')=='deliverables-v2'
    if revision==2 and not lean:raise ValueError('replacement_requires_deliverables_profile')
    if lean:
        selected=json.loads(contents['00_MANIFEST/asset-specs-selected-v1.json'])
        expected_names=versioned_core(version)|DELIVERABLE_DOCUMENTS|versioned_optional(version)|{'10_DELIVERY/'+packet['story_id']+'-v1.mp4'}
        for item in selected:
            name=item['relative_path']
            if not allowed(name) or not name.startswith(('02_MASTER_ASSETS/CHARACTERS/','03_SCENE_PACKS/')):raise ValueError('unsafe_selected_asset')
            expected_names.add(name)
            prompt=item.get('prompt_relative_path',f"06_GENERATION_SPECS/{item['asset_id']}-prompt-v1.txt")
            if not allowed(prompt) or not prompt.startswith('06_GENERATION_SPECS/'):raise ValueError('unsafe_selected_prompt')
            expected_names.add(prompt)
        project=json.loads(contents['project.json'])
        if project.get('creative_plan_schema_version')=='3.0':
            pre=project.get('preproduction') or {}
            registered_assets={row.get('id'):row for row in project.get('asset_registry',[])
                               if isinstance(row,dict) and isinstance(row.get('id'),str)}
            split_asset=registered_assets.get(pre.get('storyboard_split_asset_id'))
            if not isinstance(split_asset,dict) or split_asset.get('path') not in contents:
                raise ValueError('storyboard_split_manifest_missing')
            split_manifest=json.loads(contents[split_asset['path']])
            board_names=storyboard_deliverable_paths(project,split_manifest)
            expected_names|=board_names
            for asset in registered_assets.values():
                relative=asset.get('path')
                if relative in board_names and asset.get('status') in ('available','verified'):
                    data=contents.get(relative)
                    if (data is None or not isinstance(asset.get('sha256'),str)
                            or hashlib.sha256(data).hexdigest()!=asset['sha256']
                            or len(data)!=asset.get('size_bytes')):
                        raise ValueError('storyboard_bundle_asset_checksum')
        if set(contents)-expected_names or not DELIVERABLE_DOCUMENTS<=set(contents):raise ValueError('non_deliverable_in_archive')
    def canonical_sha(value):return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
    if manifest.get('plan_sha256')!=jobs[0]['approved_plan_sha256'] or canonical_sha(json.loads(contents[f'00_MANIFEST/creative-plan-v{version}.json']))!=jobs[0]['approved_plan_sha256']:
        raise ValueError('bundle_approved_plan_checksum')
    snapshot=json.loads(contents['01_SOURCE/source-snapshot.json'])
    if snapshot.get('source_sha256')!=jobs[0]['approved_source_sha256'] or hashlib.sha256(snapshot['source']['document_text'].encode()).hexdigest()!=jobs[0]['approved_source_sha256']:
        raise ValueError('bundle_approved_source_checksum')
    video='10_DELIVERY/'+packet['story_id']+'-v1.mp4'
    if video not in contents or hashlib.sha256(contents[video]).hexdigest()!=jobs[0]['result']['sha256']:raise ValueError('bundle_video_checksum')
    audio=Path(jobs[1]['result']['artifact_path']).resolve(strict=True)
    if not audio.is_relative_to(root) or not audio.is_file() or digest(audio)[0]!=jobs[1]['result']['sha256']:raise ValueError('bundle_audio_checksum')
    contents['07_AUDIO/voice.wav']=audio.read_bytes()
    if not lean:contents['07_AUDIO/typecast-receipt.json']=json.dumps(jobs[1]['result'],sort_keys=True,ensure_ascii=False).encode()
    def stamp(seconds):
        ms=round(seconds*1000);h,ms=divmod(ms,3600000);m,ms=divmod(ms,60000);s,ms=divmod(ms,1000)
        return f'{h:02}:{m:02}:{s:02},{ms:03}'
    cues=jobs[0]['result'].get('cues',jobs[1]['result'].get('captions',[]))
    contents['07_AUDIO/captions.srt']='\n\n'.join(f"{i}\n{stamp(c['start_seconds'])} --> {stamp(c['end_seconds'])}\n{c['text']}" for i,c in enumerate(cues,1)).encode()
    if lean:
        request=jobs[0].get('request',{}).get('payload',{})
        scene_specs=[s for s in selected if s['kind']=='scene_image']
        timeline=[]
        for spec,scene in zip(scene_specs,request.get('render_manifest',{}).get('scenes',[])):
            timeline.append({'shot_id':spec['asset_id'],'image':spec['relative_path'],
                **{k:scene[k] for k in ('start_seconds','end_seconds','camera_move') if k in scene}})
        contents['00_MANIFEST/render-settings.json']=json.dumps({'caption_style':request.get('caption_style',{}),'timeline':timeline,
            **({'listening_edit':request['render_manifest']['listening_edit']} if 'listening_edit' in request.get('render_manifest',{}) else {})},sort_keys=True,ensure_ascii=False).encode()
        edit=request.get('render_manifest',{}).get('listening_edit',{}).get('timeline',{})
        contents['07_AUDIO/word-timestamps.json']=json.dumps({'captions':edit.get('word_timestamps',cues),
            'timing_basis':edit.get('alignment_basis','provider_grouped_caption_boundaries')},sort_keys=True,ensure_ascii=False).encode()
        if 'listening_edit' in request.get('render_manifest',{}):
            raw_words=jobs[1]['result'].get('word_timestamps')
            contents['07_AUDIO/provider-word-timestamps.json']=json.dumps({'captions':raw_words or jobs[1]['result'].get('captions',[]),
                'timing_basis':'provider_word_timestamps' if raw_words else 'provider_grouped_caption_boundaries'},sort_keys=True,ensure_ascii=False).encode()
        contents['00_MANIFEST/production-receipt.json']=json.dumps({'render_job_id':rid,'tts_job_id':tid,
            'video':{k:v for k,v in jobs[0]['result'].items() if k in ('sha256','size_bytes','duration_seconds','width','height','video_codec','audio_codec')},
            'audio':{k:v for k,v in jobs[1]['result'].items() if k in ('sha256','size_bytes','duration_seconds','voice_id')}},sort_keys=True,ensure_ascii=False).encode()
    else:
        contents['09_QA/native-render-receipt.json']=json.dumps(jobs[0]['result'],sort_keys=True,ensure_ascii=False).encode()
        contents['00_MANIFEST/render-request.json']=json.dumps(jobs[0].get('request',{}),sort_keys=True,ensure_ascii=False).encode()
        contents['07_AUDIO/typecast-request.json']=json.dumps(jobs[1].get('request',{}),sort_keys=True,ensure_ascii=False).encode()
        contents['09_QA/provider-budget-ledger.json']=json.dumps({'cost_kind':'policy_reserved_estimates_not_invoices',
            'image_intents':jobs[0].get('image_ledger',[]),'provider_reservations':jobs[0].get('provider_ledger',[])},sort_keys=True,ensure_ascii=False).encode()
    manifest['entries']=[{'path':name,'sha256':hashlib.sha256(data).hexdigest(),'size_bytes':len(data)} for name,data in sorted(contents.items())]
    manifest['format']='production-deliverables-v2' if lean else 'private-production-project-v1'
    encoded=json.dumps(manifest,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()
    fd,name=tempfile.mkstemp(dir=folder,suffix='.zip');os.close(fd)
    try:
        with zipfile.ZipFile(name,'w') as out:
            for path,data in sorted(contents.items()):zip_entry(out,path,data)
            zip_entry(out,'BUNDLE_MANIFEST.json',encoded)
        sha,md5,size=digest(name)
        if size>MAX_ZIP:raise ValueError('bundle_output_too_large')
        os.chmod(name,0o600);os.link(name,final)
        value={'render_job_id':rid,'tts_job_id':tid,'story_id':packet['story_id'],'input_sha256':packet['zip_sha256'],
               'sha256':sha,'md5':md5,'size_bytes':size,'entry_count':len(contents),'manifest_sha256':hashlib.sha256(encoded).hexdigest()}
        if revision==2:value.update(bundle_revision=2,replacement_of=replacement)
        receipt.write_text(json.dumps(value,sort_keys=True));receipt.chmod(0o600)
        return value
    finally:os.unlink(name)


def main():
    try:
        raw=sys.stdin.buffer.read(360*1024*1024+1)
        if len(raw)>360*1024*1024:raise ValueError('bundle_body_limit')
        packet=json.loads(raw)
        with psycopg.connect(os.environ['DATABASE_URL'],row_factory=dict_row) as db:
            parent=db.execute("SELECT payload FROM outbox WHERE event_type='creative_production' AND aggregate_id=%s",(packet['story_id'],)).fetchone()
            jobs=[db.execute('SELECT story_id,run_id,stage,status,result,request FROM generation_jobs WHERE id=%s',(uuid.UUID(packet[k]),)).fetchone() for k in ('render_job_id','tts_job_id')]
            images=db.execute("SELECT id,status,payload FROM outbox WHERE event_type='image_call_intent' AND aggregate_id=%s ORDER BY id",(packet['story_id'],)).fetchall()
            providers=db.execute("SELECT job_id::text,provider,units,request_count,reserved_cost::text,status FROM provider_budget_reservations WHERE job_id IN (%s,%s)",tuple(uuid.UUID(packet[k]) for k in ('render_job_id','tts_job_id'))).fetchall()
        if not all(jobs) or not parent:raise ValueError('bundle_job_missing')
        jobs[0]['image_ledger']=images;jobs[0]['provider_ledger']=providers
        jobs[0]['approved_plan_sha256']=parent['payload']['plan_sha256']
        jobs[0]['approved_source_sha256']=parent['payload']['source_sha256']
        print(json.dumps(stage(packet,os.environ.get('ARTIFACT_ROOT','/data/artifacts'),jobs)))
    except Exception as error:
        print(json.dumps({'error':str(error) if isinstance(error,ValueError) and re.fullmatch('[a-z_]+',str(error)) else 'project_bundle_staging_failed'}));raise SystemExit(1)


if __name__=='__main__':main()
