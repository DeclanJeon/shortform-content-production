"""Restore an encrypted installer snapshot into a NEW isolated instance, OFF.

Never applies to an existing deployment or activates restored workflows. Native
OAuth remains encrypted in n8n; decrypted backup files stay in private staging.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Mapping
import re
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
import time
import sys
sys.dont_write_bytecode=True
from installer import atomic,command,dotenv,preflight,start,verify_source


def unpack_verified(archive_path,destination):
    """Reject links/traversal/extras and verify every byte before installation."""
    destination=Path(destination)
    with tarfile.open(archive_path,'r:*') as archive:
        infos=archive.getmembers();names=[m.name for m in infos]
        if len(names)!=len(set(names)) or sum(m.size for m in infos)>8*1024**3:raise ValueError('backup_limits')
        for m in infos:
            p=Path(m.name)
            if not m.isfile() or p.is_absolute() or '..' in p.parts or '\\' in m.name:raise ValueError('unsafe_backup_member')
        entry=archive.getmember('BACKUP-MANIFEST.json')
        if entry.size>8*1024*1024:raise ValueError('backup_manifest_limit')
        manifest=json.load(archive.extractfile(entry))
        if manifest.get('format')!='shortform-private-backup-v1' or manifest.get('restore_automation_must_remain_off') is not True:raise ValueError('unsupported_backup_format')
        entries={e['path']:e for e in manifest['entries']}
        if len(entries)!=len(manifest['entries']) or set(names)!=set(entries)|{'BACKUP-MANIFEST.json'}:raise ValueError('backup_manifest_mismatch')
        for m in infos:
            if m.name=='BACKUP-MANIFEST.json':continue
            data=archive.extractfile(m)
            out=destination/m.name;out.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            h=hashlib.sha256();size=0
            with out.open('xb') as f:
                while block:=data.read(1024*1024):h.update(block);size+=len(block);f.write(block)
            out.chmod(0o600)
            if h.hexdigest()!=entries[m.name]['sha256'] or size!=entries[m.name]['size_bytes']:raise ValueError('backup_checksum_mismatch')
    return manifest


def disable_native_workflows(path):
    with sqlite3.connect(path) as db:
        if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('n8n_snapshot_corrupt')
        columns={r[1] for r in db.execute('PRAGMA table_info(workflow_entity)')}
        if not {'active','activeVersionId'}.issubset(columns):raise ValueError('unsupported_native_deactivation_schema')
        db.execute('UPDATE workflow_entity SET active=0,activeVersionId=NULL')
        if db.execute('SELECT count(*) FROM workflow_entity WHERE active=1 OR activeVersionId IS NOT NULL').fetchone()[0]:raise ValueError('native_deactivation_failed')
        db.commit()
        db.execute('PRAGMA wal_checkpoint(TRUNCATE)')


def validate_new_identity(original: Mapping[str, object], project: str, port: int) -> None:
    if project==original['project'] or port==original['n8n_port']:
        raise ValueError('restore_requires_new_project_and_port')


def restore(backup,target,project,port,*,key_file=None,identity=None):
    target=Path(target).absolute();backup=Path(backup).resolve(strict=True)
    if any(p.is_symlink() for p in (target,*target.parents)):raise ValueError('unsafe_restore_target')
    if target.exists():raise ValueError('restore_requires_new_empty_destination')
    if not re.fullmatch('[a-z][a-z0-9_-]{2,39}',project) or type(port) is not int or not 1024<=port<=65535:raise ValueError('invalid_restore_identity')
    c={'project':project,'n8n_port':port};preflight(target,c,require_new_resources=True)
    if key_file:
        key_file=Path(key_file).resolve(strict=True)
        if key_file.stat().st_mode&0o077:raise ValueError('private_recovery_key_required')
        if not shutil.which('gpg'):raise ValueError('install_gpg_first')
        decrypt=['gpg','--no-options','--batch','--no-symkey-cache','--pinentry-mode','loopback','--passphrase-file',str(key_file),'--decrypt',str(backup)]
    elif identity:
        if not shutil.which('age'):raise ValueError('install_age_first')
        decrypt=['age','--decrypt','--identity',str(Path(identity).resolve(strict=True)),str(backup)]
    else:raise ValueError('recovery_key_required')
    with tempfile.TemporaryDirectory(prefix='shortform-restore-') as d:
        root=Path(d);root.chmod(0o700)
        if key_file:
            home=root/'gnupg';home.mkdir(mode=0o700);decrypt[1:1]=['--homedir',str(home)]
        plain=root/'backup.tar';plain.touch(mode=0o600)
        with plain.open('wb') as f:p=subprocess.run(decrypt,stdout=f,stderr=subprocess.DEVNULL,timeout=300)
        if p.returncode:raise ValueError('backup_decryption_or_integrity_failed')
        snapshot=root/'snapshot';snapshot.mkdir(mode=0o700);unpack_verified(plain,snapshot)
        state=json.loads((snapshot/'install-state.json').read_text())
        if state.get('format')!='shortform-install-v1':raise ValueError('installer_backup_required')
        if verify_source(snapshot/'source')!=state['source_manifest_sha256']:raise ValueError('restore_source_manifest_mismatch')
        validate_new_identity(state['settings'],project,port)
        c={**state['settings'],'project':project,'n8n_port':port}
        # Recheck after authenticated decryption, before creating the destination.
        preflight(target,c,require_new_resources=True)
        # One consistent offline n8n copy, deactivate BEFORE its first process.
        nroots=[p for p in (snapshot/'n8n-data',snapshot/'n8n-data/.n8n') if (p/'database.sqlite').is_file()]
        if len(nroots)!=1:raise ValueError('n8n_snapshot_database_missing')
        nroot=nroots[0]
        disable_native_workflows(nroot/'database.sqlite')
        target.mkdir(parents=True,mode=0o700)
        for name in ('source','secrets'):
            shutil.copytree(snapshot/name,target/name)
        if (snapshot/'workflows').exists():shutil.copytree(snapshot/'workflows',target/'workflows')
        else:(target/'workflows').mkdir(mode=0o755)
        (snapshot/'artifacts').mkdir(exist_ok=True,mode=0o700)
        shutil.copyfile(snapshot/'compose.yml',target/'compose.yml')
        env=dotenv(snapshot/'.env');env['N8N_PORT']=str(port)
        for key in ('AUTOMATIC_CREATIVE_ENABLED','AUTOMATIC_MEDIA_ENABLED','AUTOMATIC_POLICY_APPROVAL_ENABLED'):
            env[key]='false'
        # External executable/skill paths and old container names are not portable.
        # Preserve private recovery data without loading it as live configuration.
        for name in ('production','coordinator-state','runtime-recovery'):
            if (snapshot/name).exists():shutil.copytree(snapshot/name,target/name)
        atomic(target/'.env',''.join(k+'='+v+'\n' for k,v in env.items()))
        state.update(state='RESTORING',settings=c,automation_enabled=False,production_certified=False,
            restore_mode='SAFE_OFF',restored_from_sha256=hashlib.sha256(backup.read_bytes()).hexdigest())
        atomic(target/'install-state.json',json.dumps(state,indent=2))
        if command(target,c,'config','--quiet').returncode:raise ValueError('restore_compose_invalid')
        # Reject an edited snapshot that would enable production automatically.
        conf=command(target,c,'config','--format','json')
        if conf.returncode:raise ValueError('restore_compose_invalid')
        cfg=json.loads(conf.stdout)
        for service in cfg['services'].values():
            for key,value in service.get('environment',{}).items():
                if key.endswith('_ENABLED') and key.startswith(('AUTOMATIC_','YOUTUBE_')) and str(value).lower()!='false':raise ValueError('unsafe_automation_in_backup')
        if command(target,c,'build',timeout=1800).returncode:raise RuntimeError('restore_image_build_failed')
        if command(target,c,'up','-d','--no-build','postgres').returncode:raise RuntimeError('restore_database_start_failed')
        for _ in range(40):
            p=command(target,c,'exec','-T','postgres','pg_isready','-h','127.0.0.1','-U','shortform','-d','shortform')
            if p.returncode==0:break
            time.sleep(1)
        else:raise RuntimeError('restore_database_timeout')
        argv=['docker','compose','--project-name',project,'--env-file',str(target/'.env'),'-f',str(target/'compose.yml'),'exec','-T','postgres','pg_restore','-U','shortform','-d','shortform','--no-owner','--single-transaction','--exit-on-error']
        with (snapshot/'database.dump').open('rb') as f:p=subprocess.run(argv,cwd=target,stdin=f,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=180)
        if p.returncode:raise RuntimeError('database_restore_failed_new_instance_preserved')
        if command(target,c,'create','--no-build','n8n','worker').returncode:raise RuntimeError('restore_volume_creation_failed')
        for service,source,remote,uid in [('n8n',nroot,'/home/node/.n8n',1000),('worker',snapshot/'artifacts','/data/artifacts',10001)]:
            if command(target,c,'cp',str(source)+'/.',service+':'+remote,timeout=300).returncode:raise RuntimeError('restore_volume_copy_failed')
            ids=command(target,c,'ps','-a','-q',service).stdout.split()
            if len(ids)!=1:raise RuntimeError('restore_container_missing')
            image=subprocess.check_output(['docker','inspect',ids[0],'--format','{{.Config.Image}}'],text=True).strip()
            p=subprocess.run(['docker','run','--rm','--network','none','--read-only','--user','0:0','--volumes-from',ids[0],'--entrypoint','sh',image,'-c',f'chown -R {uid}:{uid} '+remote],capture_output=True,timeout=120)
            if p.returncode:raise RuntimeError('restore_volume_permissions_failed')
        ready=start(target,c)
        state=json.loads((target/'install-state.json').read_text());state.update(state='RESTORED_SAFE_OFF',automation_enabled=False,restore_verification='CORE_STACK_BOOTED_EXTERNAL_CREATIVE_NOT_INCLUDED')
        atomic(target/'install-state.json',json.dumps(state,indent=2))
        return {'state':state['state'],'project':project,'n8n_url':ready['n8n_url'],
            'automation_enabled':False,'publisher_included':False,'creator_runtime_verified':False}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--backup',type=Path,required=True);p.add_argument('--target',type=Path,required=True);p.add_argument('--project',required=True);p.add_argument('--n8n-port',type=int,required=True);p.add_argument('--key-file',type=Path);p.add_argument('--identity',type=Path)
    a=p.parse_args()
    try:print(json.dumps(restore(a.backup,a.target,a.project,a.n8n_port,key_file=a.key_file,identity=a.identity)))
    except (OSError,ValueError,RuntimeError,subprocess.SubprocessError):
        print(json.dumps({'state':'RESTORE_STOPPED_NEW_INSTANCE_PRESERVED','automation_enabled':False}));raise SystemExit(1)

if __name__=='__main__':main()
