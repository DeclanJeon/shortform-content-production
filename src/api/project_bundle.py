"""Register/stream a content-checked private production ZIP, not arbitrary files."""
import hashlib
import hmac
import json
from pathlib import Path
import re
import uuid
from archive_receipt import verified_archive
from automatic_archive import GoogleFile
from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel,ConfigDict,Field


class BundleReceipt(BaseModel):
    model_config=ConfigDict(extra='forbid')
    story_id:str=Field(pattern=r'^[0-9]{8}-auto-[0-9a-f]{12}$')
    render_job_id:uuid.UUID
    tts_job_id:uuid.UUID
    input_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
    sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
    md5:str=Field(pattern=r'^[0-9a-f]{32}$')
    size_bytes:int=Field(gt=0,le=256*1024*1024,strict=True)
    entry_count:int=Field(ge=6,le=1010,strict=True)
    manifest_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
    bundle_revision:int=Field(default=1,ge=1,le=2,strict=True)
    replacement_of:int|None=Field(default=None,gt=0,strict=True)


class EmptyRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')


class RetirementReceipt(BaseModel):
    model_config=ConfigDict(extra='forbid')
    google_file:GoogleFile


def create_project_bundle_router(connection,token,artifact_root):
    router=APIRouter()
    def auth(value):
        try:ok=isinstance(value,str) and value.startswith('Bearer ') and hmac.compare_digest(value[7:].encode('ascii'),token.encode('ascii'))
        except UnicodeEncodeError:ok=False
        if not ok:raise HTTPException(401,'unauthorized')
    def path_for(story,render,revision=1):
        root=Path(artifact_root).resolve(strict=True)
        path=root/'project-bundles'/story/(str(render)+('-deliverables-v2' if revision==2 else '')+'.zip')
        if path.is_symlink() or not path.resolve().is_relative_to(root):raise HTTPException(409,'bundle_path_invalid')
        return path
    def checksum(file):
        h=hashlib.sha256();m=hashlib.md5();size=0
        while chunk:=file.read(1024*1024):h.update(chunk);m.update(chunk);size+=len(chunk)
        file.seek(0);return h.hexdigest(),m.hexdigest(),size

    @router.post('/v1/project-bundles/register')
    def register(body:BundleReceipt,authorization:str|None=Header(default=None)):
        auth(authorization);data=body.model_dump(mode='json',exclude_defaults=True)
        if (body.bundle_revision==2)!=(body.replacement_of is not None):raise HTTPException(422,'replacement_identity_required')
        path=path_for(body.story_id,body.render_job_id,body.bundle_revision)
        try:
            staged=json.loads(path.with_suffix('.json').read_text())
            with path.open('rb') as f:actual=checksum(f)
        except (OSError,ValueError):raise HTTPException(409,'bundle_not_staged') from None
        if staged!=data or actual!=(body.sha256,body.md5,body.size_bytes):raise HTTPException(409,'bundle_checksum_mismatch')
        with connection() as db:
            child=db.execute("SELECT id,payload FROM outbox WHERE event_type='creative_plan_ready' AND aggregate_id=%s AND status='PRIVATE_VIDEO_READY' FOR UPDATE",(body.story_id,)).fetchone()
            if not child:raise HTTPException(409,'private_video_required')
            parent=db.execute("SELECT id,payload FROM outbox WHERE id=%s AND event_type='creative_production' FOR UPDATE",(int(child['payload']['task_id']),)).fetchone()
            if not parent:raise HTTPException(409,'bundle_parent_required')
            p=parent['payload'];result=p.get('media_result',{})
            if result.get('render_job_id')!=str(body.render_job_id) or result.get('tts_job_id')!=str(body.tts_job_id):raise HTTPException(409,'bundle_media_lineage')
            key='automatic-project-archive:'+body.story_id+':'+str(body.render_job_id)
            if body.bundle_revision==2:key+=':replacement-v2:'+str(body.replacement_of)
            old=db.execute("SELECT id,payload FROM outbox WHERE event_type='automatic_project_archive' AND dedup_key=%s FOR UPDATE",(key,)).fetchone()
            if old:
                if old['payload']['sha256']!=body.sha256:raise HTTPException(409,'bundle_version_conflict')
                return {'state':'BUNDLE_REGISTERED','archive_id':str(old['id']),'sha256':body.sha256}
            if body.bundle_revision==2:
                previous=db.execute("SELECT id,aggregate_id,status,payload FROM outbox WHERE id=%s AND event_type='automatic_project_archive' FOR UPDATE",(body.replacement_of,)).fetchone()
                if not previous or previous['aggregate_id']!=body.story_id or previous['payload'].get('render_job_id')!=str(body.render_job_id) or previous['payload'].get('bundle_revision',1)!=1:raise HTTPException(409,'replacement_lineage_invalid')
                try:verified_archive(previous,previous['payload'],kind='project')
                except ValueError:raise HTTPException(409,'replacement_requires_verified_archive') from None
                release=db.execute("SELECT payload FROM outbox WHERE event_type='manual_youtube_release' AND aggregate_id=%s AND status='PUBLISHED' ORDER BY id DESC LIMIT 1",(body.story_id,)).fetchone()
                if (not release or release['payload'].get('public_status_verified') is not True
                        or release['payload'].get('manifest',{}).get('render_job_id')!=str(body.render_job_id)
                        or release['payload'].get('manifest',{}).get('sha256')!=result.get('sha256')):raise HTTPException(409,'replacement_requires_published_video')
            payload={**data,'publication_authorized':False,'archive_kind':'project'}
            row=db.execute("INSERT INTO outbox(event_type,aggregate_id,payload,dedup_key,status) VALUES('automatic_project_archive',%s,%s,%s,'PENDING') RETURNING id",(body.story_id,json.dumps(payload),key)).fetchone()
            if body.bundle_revision==2:
                return {'state':'BUNDLE_REGISTERED','archive_id':str(row['id']),'sha256':body.sha256,'replacement_pending':True}
            p['project_archive_required']=True;p['project_bundle_sha256']=body.sha256
            db.execute('UPDATE outbox SET payload=%s WHERE id=%s',(json.dumps(p,ensure_ascii=False),parent['id']))
            video=db.execute("SELECT id,payload FROM outbox WHERE event_type='automatic_video_archive' AND aggregate_id=%s FOR UPDATE",(body.story_id,)).fetchone()
            if video:
                v=video['payload'];v['project_archive_required']=True
                db.execute('UPDATE outbox SET payload=%s WHERE id=%s',(json.dumps(v),video['id']))
        return {'state':'BUNDLE_REGISTERED','archive_id':str(row['id']),'sha256':body.sha256}

    @router.get('/v1/project-bundles/{render_id}/artifact')
    def artifact(render_id:uuid.UUID,archive_id:int|None=None,authorization:str|None=Header(default=None)):
        auth(authorization)
        with connection() as db:
            if archive_id is None:
                row=db.execute("SELECT payload FROM outbox WHERE event_type='automatic_project_archive' AND payload->>'render_job_id'=%s AND status<>'SUPERSEDED' ORDER BY id DESC LIMIT 1",(str(render_id),)).fetchone()
            else:
                row=db.execute("SELECT payload FROM outbox WHERE event_type='automatic_project_archive' AND payload->>'render_job_id'=%s AND id=%s",(str(render_id),archive_id)).fetchone()
        if not row:raise HTTPException(404,'project_bundle_not_registered')
        p=row['payload'];path=path_for(p['story_id'],render_id,p.get('bundle_revision',1))
        try:
            file=path.open('rb')
            if checksum(file)!=(p['sha256'],p['md5'],p['size_bytes']):file.close();raise HTTPException(409,'bundle_checksum_mismatch')
        except OSError:raise HTTPException(409,'bundle_unavailable') from None
        def chunks():
            try:
                while chunk:=file.read(1024*1024):yield chunk
            finally:file.close()
        return StreamingResponse(chunks(),media_type='application/zip',headers={
            'Content-Length':str(p['size_bytes']),'X-Artifact-SHA256':p['sha256'],
            'X-Artifact-Size-Bytes':str(p['size_bytes']),'X-Artifact-Codec':'zip'})
    def replacement_rows(db,archive_id):
        new=db.execute("SELECT id,aggregate_id,status,payload FROM outbox WHERE id=%s AND event_type='automatic_project_archive' FOR UPDATE",(archive_id,)).fetchone()
        if not new or new['payload'].get('bundle_revision')!=2:raise HTTPException(409,'replacement_not_registered')
        try:verified_archive(new,new['payload'],kind='project')
        except ValueError:raise HTTPException(409,'replacement_not_archived') from None
        old=db.execute("SELECT id,aggregate_id,status,payload FROM outbox WHERE id=%s AND event_type='automatic_project_archive' FOR UPDATE",(new['payload']['replacement_of'],)).fetchone()
        if (not old or old['aggregate_id']!=new['aggregate_id'] or old['payload'].get('render_job_id')!=new['payload']['render_job_id']):raise HTTPException(409,'replacement_lineage_invalid')
        return new,old

    @router.post('/v1/project-bundles/replacements/{archive_id}/promote')
    def promote(archive_id:int,body:EmptyRequest,authorization:str|None=Header(default=None)):
        auth(authorization)
        with connection() as db:
            new,old=replacement_rows(db,archive_id);n=new['payload'];o=old['payload']
            if old['status']=='SUPERSEDED':
                if o.get('superseded_by')!=archive_id:raise HTTPException(409,'different_archive_is_canonical')
            else:
                try:verified_archive(old,o,kind='project')
                except ValueError:raise HTTPException(409,'old_archive_not_verified') from None
                parent=db.execute("SELECT id,payload FROM outbox WHERE event_type='creative_production' AND aggregate_id=%s FOR UPDATE",(new['aggregate_id'],)).fetchone()
                release=db.execute("SELECT payload FROM outbox WHERE event_type='manual_youtube_release' AND aggregate_id=%s AND status='PUBLISHED' ORDER BY id DESC LIMIT 1",(new['aggregate_id'],)).fetchone()
                if not parent or not release or release['payload'].get('public_status_verified') is not True or release['payload'].get('manifest',{}).get('render_job_id')!=n['render_job_id']:raise HTTPException(409,'replacement_requires_published_video')
                p=parent['payload']
                if p.get('project_bundle_sha256')!=o['sha256']:raise HTTPException(409,'replacement_canonical_conflict')
                p.update(project_bundle_sha256=n['sha256'],active_project_archive_id=archive_id)
                o.update(superseded_by=archive_id,successor_sha256=n['sha256'])
                n.update(canonical_replacement=True,obsolete_google_file=o['archive_google_file'])
                db.execute("UPDATE outbox SET status='SUPERSEDED',payload=%s WHERE id=%s",(json.dumps(o),old['id']))
                db.execute('UPDATE outbox SET payload=%s WHERE id=%s',(json.dumps(n),archive_id))
                db.execute('UPDATE outbox SET payload=%s WHERE id=%s',(json.dumps(p),parent['id']))
        return {'state':'REPLACEMENT_CANONICAL','archive_id':archive_id,'trash_authorized':True,'obsolete_google_file':o['archive_google_file']}

    @router.post('/v1/project-bundles/replacements/{archive_id}/retired')
    def retired(archive_id:int,body:RetirementReceipt,authorization:str|None=Header(default=None)):
        auth(authorization);actual=body.google_file.model_dump()
        with connection() as db:
            new,old=replacement_rows(db,archive_id)
            expected={**old['payload'].get('archive_google_file',{}),'trashed':True}
            if old['status']!='SUPERSEDED' or old['payload'].get('superseded_by')!=archive_id or actual!=expected:raise HTTPException(409,'obsolete_archive_retirement_not_verified')
            p=new['payload'];p.update(obsolete_file_retired=True,obsolete_google_file_readback=actual)
            db.execute('UPDATE outbox SET payload=%s WHERE id=%s',(json.dumps(p),archive_id))
        return {'state':'REPLACEMENT_COMPLETE','archive_id':archive_id,'old_file_trashed':True,'publication_changed':False}
    return router
