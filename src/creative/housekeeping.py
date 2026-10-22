"""Bounded completion reconciliation; no generation, upload or DB mutation."""
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import shlex
from .worker import SID
from .deliverables import checked_path
from .pi_runtime import _atomic_json
from .retention import prune,validate_completion

SNAPSHOT=r'''
import base64,hashlib,json,os,sys,uuid,zipfile
from pathlib import Path
import psycopg
from psycopg.rows import dict_row
from archive_receipt import verified_archive
story=json.load(sys.stdin)['story_id']
with psycopg.connect(os.environ['DATABASE_URL'],row_factory=dict_row) as db:
 release=db.execute("SELECT id,aggregate_id,status,payload FROM outbox WHERE event_type='manual_youtube_release' AND aggregate_id=%s AND status='PUBLISHED' ORDER BY id DESC LIMIT 1",(story,)).fetchone()
 archive=db.execute("SELECT id,aggregate_id,status,payload FROM outbox WHERE event_type='automatic_project_archive' AND aggregate_id=%s AND status='ARCHIVED' ORDER BY id DESC LIMIT 1",(story,)).fetchone()
if not release or not archive:print(json.dumps({'status':'WAITING_FOR_COMPLETION'}));raise SystemExit(0)
a=archive['payload'];verified_archive(archive,a,kind='project')
rid=str(uuid.UUID(a['render_job_id']));root=Path(os.environ.get('ARTIFACT_ROOT','/data/artifacts')).resolve(strict=True)
p=root/'project-bundles'/story/(rid+('-deliverables-v2' if a.get('bundle_revision')==2 else '')+'.zip')
if p.is_symlink() or not p.resolve().is_relative_to(root):raise ValueError('unsafe_completion_bundle')
with p.open('rb') as f:
 if hashlib.file_digest(f,'sha256').hexdigest()!=a['sha256']:raise ValueError('completion_bundle_checksum')
with zipfile.ZipFile(p) as z:
 raw=z.read('BUNDLE_MANIFEST.json');manifest=json.loads(raw)
 if manifest.get('retention_profile')!='deliverables-v2':print(json.dumps({'status':'LEGACY_ARCHIVE_REQUIRES_MIGRATION'}));raise SystemExit(0)
 if hashlib.sha256(raw).hexdigest()!=a['manifest_sha256']:raise ValueError('completion_manifest_checksum')
 entries={e['path']:e for e in manifest['entries']};files={}
 for name in ('07_AUDIO/voice.wav','07_AUDIO/captions.srt','07_AUDIO/word-timestamps.json','00_MANIFEST/render-settings.json','00_MANIFEST/production-receipt.json'):
  data=z.read(name)
  if len(data)>20*1024*1024 or hashlib.sha256(data).hexdigest()!=entries[name]['sha256']:raise ValueError('completion_audio_checksum')
  files[name]={'sha256':entries[name]['sha256'],'base64':base64.b64encode(data).decode()}
print(json.dumps({'status':'COMPLETION_VERIFIED','release':release,'archive':archive,'files':files}))
'''


def snapshot(gateway,story):
    if not SID.fullmatch(story):raise ValueError('invalid_story_id')
    prefix=[] if gateway.host=='local' else ['ssh','-T','-o','BatchMode=yes','-o','ConnectTimeout=10',gateway.host]
    command=['docker','exec','-i',gateway.gateway_container,'python','-c',SNAPSHOT]
    command=command if gateway.host=='local' else prefix+[shlex.join(command)]
    p=subprocess.run(command,input=json.dumps({'story_id':story}),text=True,capture_output=True,timeout=60)
    if p.returncode or len(p.stdout)>32*1024*1024:raise RuntimeError('completion_snapshot_unavailable')
    return json.loads(p.stdout)


def reconcile(root,packet,*,apply=False):
    if Path(root).is_symlink():raise ValueError('unsafe_project_root')
    root=Path(root).resolve(strict=True)
    if packet.get('status')!='COMPLETION_VERIFIED':return {'story_id':root.name,'status':packet.get('status','WAITING_FOR_COMPLETION')}
    specs=json.loads(checked_path(root,'00_MANIFEST/asset-specs-selected-v1.json').read_text())
    result=json.loads(checked_path(root,'10_DELIVERY/automatic-media-result-v1.json').read_text())
    validate_completion(root,packet['release'],packet['archive'],result)
    expected={'07_AUDIO/voice.wav','07_AUDIO/captions.srt','07_AUDIO/word-timestamps.json','00_MANIFEST/render-settings.json','00_MANIFEST/production-receipt.json'}
    if set(packet['files'])!=expected:raise ValueError('missing_completed_deliverable')
    for name,entry in packet['files'].items():
        if name not in ('07_AUDIO/voice.wav','07_AUDIO/captions.srt','07_AUDIO/word-timestamps.json','00_MANIFEST/render-settings.json','00_MANIFEST/production-receipt.json'):raise ValueError('unexpected_completed_deliverable')
        data=base64.b64decode(entry['base64'],validate=True)
        if hashlib.sha256(data).hexdigest()!=entry['sha256']:raise ValueError('completed_deliverable_checksum')
        p=checked_path(root,name)
        if p.exists():
            if hashlib.sha256(p.read_bytes()).hexdigest()!=entry['sha256']:raise ValueError('local_deliverable_conflict')
        elif apply:
            p.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            with p.open('xb') as f:f.write(data)
            p.chmod(0o600)
    outcome=prune(root,specs,result,packet['release'],packet['archive'],apply=apply)
    receipt={'story_id':root.name,'status':'CLEANED' if apply else 'CLEANUP_DRY_RUN','profile':outcome['profile'],
        'files_removed':outcome['file_count'],'bytes_removed':outcome['bytes'],
        'youtube_video_id':packet['release']['payload']['video_id'],'archive_sha256':packet['archive']['payload']['sha256']}
    if apply:_atomic_json(checked_path(root,'10_DELIVERY/retention-receipt-v1.json'),receipt)
    return receipt


def once(config,gateway):
    base=Path(config['production_root']).resolve(strict=True)
    for root in sorted(base.iterdir()):
        if root.is_symlink() or not root.is_dir() or not SID.fullmatch(root.name):continue
        if (root/'10_DELIVERY/retention-receipt-v1.json').exists() or not (root/'10_DELIVERY/project-bundle-registration-v1.json').exists():continue
        packet=snapshot(gateway,root.name)
        if packet.get('status')=='COMPLETION_VERIFIED':return reconcile(root,packet,apply=True)
    return {'status':'NO_COMPLETED_RETENTION_TASKS'}
