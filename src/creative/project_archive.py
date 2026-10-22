"""Build a cached, allowlisted project ZIP and enqueue native-OAuth archival."""
import argparse
import fcntl
import base64
import hashlib
import json
import os
from pathlib import Path
import tempfile
import zipfile
from worker.adapters.project_bundle_contract import MAX_ZIP
from .deliverables import PROFILE,checked_path,collect,write_documents
from .media_runtime import _atomic,_sha
from .worker import Gateway,SID


def build_source(root,task,result,replacement_of=None,*,skill_root=None):
    root=Path(root).resolve(strict=True);base=root/'00_MANIFEST/.archive-runtime'
    base.mkdir(parents=True,exist_ok=True,mode=0o700)
    suffix='-replacement-'+str(replacement_of) if replacement_of is not None else ''
    output=base/('project-deliverables-v2'+suffix+'.zip');checkpoint=base/('deliverables-receipt-v2'+suffix+'.json')
    project=json.loads((root/'project.json').read_text(encoding='utf-8'))
    if project.get('creative_plan_schema_version')=='3.0':
        if not isinstance(skill_root,str) or not skill_root:
            raise ValueError('storyboard_skill_root_required')
        from .storyboard_project import validate_full_storyboard
        validate_full_storyboard(project,root,skill_root)
    if checkpoint.exists():
        old=json.loads(checkpoint.read_text())
        if old['render_job_id']!=result['render_job_id'] or old['sha256']!=_sha(output):raise RuntimeError('project_bundle_cache_conflict')
        return output,old
    specs=json.loads((root/'00_MANIFEST/asset-specs-selected-v1.json').read_text())
    files=collect(root,task['story_id'],specs)
    entries=[{'path':name,'sha256':_sha(path),'size_bytes':path.stat().st_size} for name,path in sorted(files)]
    manifest={'story_id':task['story_id'],'render_job_id':result['render_job_id'],'tts_job_id':result['tts_job_id'],
              'plan_sha256':task['plan_sha256'],'plan_version':task.get('plan_version',1),
              'schema_version':'1.0','retention_profile':PROFILE,'entries':entries}
    fd,name=tempfile.mkstemp(dir=base,suffix='.zip');os.close(fd)
    try:
        with zipfile.ZipFile(name,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as out:
            for relative,path in sorted(files):
                info=zipfile.ZipInfo(relative,date_time=(2026,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED;info.external_attr=0o100600<<16
                out.writestr(info,path.read_bytes())
            info=zipfile.ZipInfo('BUNDLE_MANIFEST.json',date_time=(2026,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED;info.external_attr=0o100600<<16
            out.writestr(info,json.dumps(manifest,sort_keys=True,ensure_ascii=False))
        if Path(name).stat().st_size>MAX_ZIP:raise RuntimeError('project_bundle_too_large')
        os.chmod(name,0o600);os.replace(name,output)
    finally:
        if os.path.exists(name):os.unlink(name)
    receipt={'sha256':_sha(output),'render_job_id':result['render_job_id'],'file_count':len(files)}
    _atomic(checkpoint,receipt)
    return output,receipt


def archive_project(root,task,result,gateway,replacement_of=None,*,skill_root=None):
    if Path(root).is_symlink():raise RuntimeError('unsafe_project_root')
    root=Path(root).resolve(strict=True);base=root/'00_MANIFEST/.archive-runtime'
    base.mkdir(parents=True,exist_ok=True,mode=0o700)
    if not base.resolve().is_relative_to(root):raise RuntimeError('unsafe_archive_directory')
    with open(base/'bundle.lock','a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        return _archive_project(root,task,result,gateway,replacement_of,skill_root=skill_root)


def _archive_project(root,task,result,gateway,replacement_of=None,*,skill_root=None):
    root=Path(root).resolve(strict=True)
    if replacement_of is not None and (type(replacement_of) is not int or replacement_of<=0):raise ValueError('invalid_replacement_archive_id')
    cached=root/('10_DELIVERY/project-bundle-registration-v1.json' if replacement_of is None else '10_DELIVERY/project-bundle-replacement-of-'+str(replacement_of)+'.json')
    if cached.exists():return json.loads(cached.read_text())
    parent=gateway.call('/v1/automation/tasks/'+task['task_id'])
    snapshot={'story_id':task['story_id'],'source_sha256':parent['source_sha256'],
              'source_sha256_kind':'document_text_utf8','source':parent['source']}
    _atomic(root/'01_SOURCE/source-snapshot.json',snapshot)
    _atomic(root/'09_QA/production-approval.json',task['production_approval'])
    specs=json.loads((root/'00_MANIFEST/asset-specs-selected-v1.json').read_text())
    write_documents(root,task['plan'],specs,result)
    source,receipt=build_source(root,task,result,replacement_of,skill_root=skill_root)
    extra={'bundle_revision':2,'replacement_of':replacement_of} if replacement_of is not None else {}
    packed=gateway.worker_rpc('stage_project_bundle.py',{'story_id':task['story_id'],**extra,
        'render_job_id':result['render_job_id'],'tts_job_id':result['tts_job_id'],
        'zip_sha256':receipt['sha256'],'zip_base64':base64.b64encode(source.read_bytes()).decode()})
    registered=gateway.call('/v1/project-bundles/register',packed)
    if registered.get('state')!='BUNDLE_REGISTERED' or registered.get('sha256')!=packed['sha256']:
        raise RuntimeError('project_bundle_registration_failed')
    _atomic(root/('10_DELIVERY/project-bundle-receipt-v1.json' if replacement_of is None else '10_DELIVERY/project-bundle-replacement-receipt-'+str(replacement_of)+'.json'),packed)
    _atomic(cached,registered)
    return registered


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',type=Path,required=True);p.add_argument('--media-id',type=int,required=True)
    a=p.parse_args();config=json.loads(a.config.read_text());gateway=Gateway(config['ssh_alias'])
    task=gateway.call('/v1/automation/media-tasks/'+str(a.media_id))
    if task['state']!='PRIVATE_VIDEO_READY' or not SID.fullmatch(task['story_id']):raise SystemExit('verified private video required')
    root=Path(config['production_root'])/task['story_id'];result=json.loads((root/'10_DELIVERY/automatic-media-result-v1.json').read_text())
    print(json.dumps(archive_project(root,task,result,gateway,skill_root=config.get('skill_root'))))


if __name__=='__main__':main()
