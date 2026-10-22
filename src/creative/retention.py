"""Prune disposable project data only after verified archival and publication.

Callers supply canonical DB rows, not a human-QA flag. The publisher's durable
IDs/checksums/approvals stay in PostgreSQL; this never deletes database records.
Defaults to a dry run. No active or unfinished episode is eligible.
"""
import hashlib
from pathlib import Path
import re
from .deliverables import collect,checked_path

RUNTIME_PREFIXES=('.history/','00_MANIFEST/.media-runtime/','00_MANIFEST/.creative-runtime/',
                  '00_MANIFEST/.policy-runtime/','00_MANIFEST/.archive-runtime/',
                  '09_QA/ai-review-proofs/','09_QA/review-boards/')
RECEIPTS={'10_DELIVERY/automatic-media-receipt-v1.json',
          '10_DELIVERY/project-bundle-registration-v1.json','10_DELIVERY/project-bundle-receipt-v1.json',
          '10_DELIVERY/retention-receipt-v1.json','07_AUDIO/voice.wav','07_AUDIO/captions.srt',
          '07_AUDIO/word-timestamps.json','00_MANIFEST/render-settings.json','00_MANIFEST/production-receipt.json'}


def validate_completion(root,release,archive,result):
    if (release.get('status')!='PUBLISHED' or archive.get('status')!='ARCHIVED'
            or release.get('aggregate_id')!=root.name or archive.get('aggregate_id')!=root.name):
        raise ValueError('completed_archived_episode_required')
    r=release['payload'];a=archive['payload'];g=a.get('archive_google_file',{})
    rid=result['render_job_id']
    if (r.get('public_status_verified') is not True or not re.fullmatch(r'[A-Za-z0-9_-]{11}',r.get('video_id',''))
            or r.get('manifest',{}).get('render_job_id')!=rid or a.get('render_job_id')!=rid
            or a.get('archived_sha256')!=a.get('sha256')
            or not re.fullmatch('[0-9a-f]{64}',a.get('sha256',''))
            or g.get('id')!=a.get('archive_drive_file_id') or not g.get('id')
            or g.get('size')!=a.get('size_bytes') or not g.get('size')
            or g.get('md5Checksum')!=a.get('md5') or not g.get('md5Checksum')
            or g.get('name')!=a.get('archive_file_name') or not g.get('name')
            or g.get('parents')!=[a.get('archive_folder_id')] or not a.get('archive_folder_id')
            or g.get('mimeType')!='application/zip' or g.get('trashed') is not False):
        raise ValueError('retention_lineage_or_receipt_invalid')
    path=checked_path(root,'10_DELIVERY/'+root.name+'-v1.mp4')
    sha=hashlib.sha256(path.read_bytes()).hexdigest()
    if sha!=result['sha256'] or sha!=r.get('manifest',{}).get('sha256'):
        raise ValueError('retained_video_checksum_mismatch')


def prune(root,specs,result,release,archive,*,apply=False):
    if Path(root).is_symlink():raise ValueError('unsafe_project_root')
    root=Path(root).resolve(strict=True)
    if not re.fullmatch(r'[0-9]{8}-auto-[0-9a-f]{12}',root.name):raise ValueError('invalid_project_id')
    validate_completion(root,release,archive,result)
    keep={name for name,_ in collect(root,root.name,specs)}|RECEIPTS
    before={name:hashlib.sha256(checked_path(root,name).read_bytes()).hexdigest()
            for name in keep if checked_path(root,name).is_file()}
    candidates=[]
    for path in root.rglob('*'):
        name=path.relative_to(root).as_posix()
        disposable=(name.startswith(RUNTIME_PREFIXES)
            or bool(re.fullmatch(r'06_GENERATION_SPECS/(?:CH|SH)[0-9]{2}-prompt-v[12]\.txt',name))
            or name in {'00_MANIFEST/asset-specs-v1.json','00_MANIFEST/creative-result-v1.json',
                        '00_MANIFEST/creative-plan-receipt-v1.json','00_MANIFEST/scene-lettering-v1.json'}
            or bool(re.fullmatch(r'08_ANIMATIC_EDIT/(?:staged-)?SH[0-9]{2}\.png',name))
            or bool(re.fullmatch(r'09_QA/render-frame-[0-9]+\.png',name))
            or bool(re.fullmatch(r'03_SCENE_PACKS/SH[0-9]{2}(?:-v[12])?(?:-lettered(?:-[0-9a-f]{8})?)?\.png',name))
            or bool(re.fullmatch(r'02_MASTER_ASSETS/CHARACTERS/CH[0-9]{2}-identity-v[12]\.png',name)))
        if not disposable or name in keep:continue
        checked_path(root,name)  # Reject links before deleting anything.
        if path.is_file():candidates.append((name,path.stat().st_size))
    if apply:
        for name,_ in candidates:checked_path(root,name).unlink()
        for name,sha in before.items():
            if hashlib.sha256(checked_path(root,name).read_bytes()).hexdigest()!=sha:
                raise RuntimeError('retained_output_changed')
    return {'profile':'deliverables-v2','applied':apply,'file_count':len(candidates),
            'bytes':sum(size for _,size in candidates),'paths':[name for name,_ in candidates]}
