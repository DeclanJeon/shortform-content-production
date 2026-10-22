"""Encrypted local recovery snapshot of an installer-managed instance only."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
sys.dont_write_bytecode=True
import tarfile
import tempfile
from installer import command


def backup(target,output,recipient=None,key_file=None):
    target=Path(target).resolve(strict=True);output=Path(output).absolute()
    state=json.loads((target/'install-state.json').read_text())
    if state.get('format')!='shortform-install-v1':raise ValueError('installer_managed_instance_required')
    if key_file is not None:
        key_file=Path(key_file).absolute()
        if not shutil.which('gpg'):raise ValueError('install_gpg_first')
        if any(p.is_symlink() for p in (key_file,*key_file.parents)) or not key_file.is_file() or key_file.stat().st_mode&0o077 or key_file.stat().st_size<32:raise ValueError('private_recovery_key_required')
        encrypt=['gpg','--no-options','--batch','--no-symkey-cache','--pinentry-mode','loopback','--passphrase-file',str(key_file),'--cipher-algo','AES256','--s2k-digest-algo','SHA512','--s2k-mode','3','--s2k-count','65011712','--compress-algo','none','--symmetric','--output','-']
    else:
        if not isinstance(recipient,str) or not re.fullmatch(r'age1[0-9a-z]{40,100}',recipient):raise ValueError('age_recipient_required')
        if not shutil.which('age'):raise ValueError('install_age_first')
        encrypt=['age','--recipient',recipient]
    if output.exists() or output.is_relative_to(target):raise ValueError('separate_new_backup_destination_required')
    c=state['settings']
    if any((target/'coordinator-state'/n).exists() for n in ('pending-task.json','pending-media.json')):
        raise ValueError('wait_for_active_checkpoints_before_backup')
    query="SELECT (SELECT count(*) FROM generation_jobs WHERE status IN ('queued','running'))+(SELECT count(*) FROM outbox WHERE status IN ('PLANNING','MEDIA_RUNNING','UPLOAD_IN_PROGRESS','PRIVATE_UPLOADED','PREPARED','ARCHIVE_IN_PROGRESS','ARCHIVING') OR (event_type='publish_sheet_handoff' AND status='IN_PROGRESS'))"
    busy=command(target,c,'exec','-T','postgres','psql','-U','shortform','-d','shortform','-Atc',query)
    if busy.returncode or busy.stdout.strip()!='0':raise ValueError('wait_for_active_jobs_before_backup')
    ids=command(target,c,'ps','-q','n8n').stdout.split()
    if len(ids)!=1:raise ValueError('n8n_not_running')
    paused=subprocess.run(['docker','inspect','--format','{{.State.Paused}}',ids[0]],capture_output=True,text=True,timeout=10)
    if paused.returncode or paused.stdout.strip()!='false':raise ValueError('n8n_already_paused_no_state_changes')
    output.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    partial=output.with_name(output.name+'.partial')
    if partial.exists():raise ValueError('partial_backup_requires_inspection')
    with tempfile.TemporaryDirectory(prefix='shortform-backup-') as d,tempfile.TemporaryDirectory(prefix='shortform-gpg-') as g:
        root=Path(d);root.chmod(0o700)
        if key_file is not None:encrypt[1:1]=['--homedir',g]
        # Snapshot private input files; never include them in the public source package.
        for name in ('.env','secrets','workflows','install-state.json','compose.yml','source'):
            source=target/name;destination=root/name
            if source.is_symlink():raise ValueError('backup_source_link_forbidden')
            if name=='secrets':
                destination.mkdir(mode=0o700)
                for secret in ('review_callback_secret','typecast_api_key'):
                    item=source/secret
                    if item.is_symlink():raise ValueError('backup_source_link_forbidden')
                    try:data=item.read_bytes()
                    except PermissionError:
                        ids=command(target,c,'ps','-q','worker').stdout.split()
                        if len(ids)!=1:raise ValueError('worker_not_running')
                        image=subprocess.check_output(['docker','inspect',ids[0],'--format','{{.Config.Image}}'],text=True).strip()
                        data=subprocess.check_output(['docker','run','--rm','--network','none','--read-only','--user','0:0','--mount','type=bind,source='+str(item)+',target=/input,readonly','--entrypoint','python',image,'-c',"from pathlib import Path;import sys;sys.stdout.buffer.write(Path('/input').read_bytes())"])
                    (destination/secret).write_bytes(data)
            elif source.is_dir():shutil.copytree(source,destination,symlinks=True)
            else:shutil.copyfile(source,destination)
        for p in root.rglob('*'):
            if p.is_symlink():raise ValueError('backup_source_link_forbidden')
            if p.is_file():p.chmod(0o600)
        # Pause the writer while copying SQLite plus WAL and encryption configuration.
        paused_services=[]
        writers=['worker','n8n']
        if (target/'runtime-compose.json').exists() and command(target,c,'ps','-q','coordinator').stdout.strip():
            writers.insert(0,'coordinator')
        try:
            for service in writers:
                current=command(target,c,'ps','-q',service).stdout.split()
                if len(current)!=1:raise ValueError('backup_writer_not_running')
                before=subprocess.run(['docker','inspect','--format','{{.State.Paused}}',current[0]],capture_output=True,text=True,timeout=10)
                if before.returncode or before.stdout.strip()!='false':raise ValueError('writer_already_paused_no_state_changes')
                if command(target,c,'pause',service).returncode:raise RuntimeError('backup_pause_failed')
                paused_services.append(service)
            busy=command(target,c,'exec','-T','postgres','psql','-U','shortform','-d','shortform','-Atc',query)
            if busy.returncode or busy.stdout.strip()!='0':raise ValueError('activity_started_during_backup')
            if any((target/'coordinator-state'/n).exists() for n in ('pending-task.json','pending-media.json')):
                raise ValueError('activity_started_during_backup')
            for name in ('production','coordinator-state'):
                if (target/name).exists():shutil.copytree(target/name,root/name,symlinks=True)
            if (target/'runtime-compose.json').exists():
                recovery=root/'runtime-recovery';recovery.mkdir(mode=0o700)
                shutil.copyfile(target/'creative-runtime.json',recovery/'creative-runtime.json')
                overlay=json.loads((target/'runtime-compose.json').read_text())
                auth=[m['source'] for m in overlay['services']['coordinator']['volumes'] if m.get('target')=='/auth']
                if len(auth)!=1:raise ValueError('runtime_auth_mount_unknown')
                shutil.copytree(auth[0],recovery/'pi-auth',symlinks=True)
            if command(target,c,'cp','n8n:/home/node/.n8n',str(root/'n8n-data')).returncode:raise RuntimeError('n8n_snapshot_failed')
            argv=['docker','compose','--project-name',c['project'],'--env-file',str(target/'.env'),'-f',str(target/'compose.yml'),
                  'exec','-T','postgres','pg_dump','-U','shortform','-d','shortform','-Fc']
            with (root/'database.dump').open('wb') as f:
                p=subprocess.run(argv,cwd=target,stdout=f,stderr=subprocess.DEVNULL,timeout=120)
            if p.returncode:raise RuntimeError('database_snapshot_failed')
            if command(target,c,'cp','worker:/data/artifacts',str(root/'artifacts'),timeout=300).returncode:raise RuntimeError('artifact_snapshot_failed')
        finally:
            failures=[]
            for service in reversed(paused_services):
                if command(target,c,'unpause',service).returncode:failures.append(service)
            if failures:raise RuntimeError('backup_services_unpause_required')
        entries=[]
        for p in sorted(root.rglob('*')):
            if p.is_symlink():raise ValueError('backup_source_link_forbidden')
            if p.is_file():
                h=hashlib.sha256()
                with p.open('rb') as f:
                    while block:=f.read(1024*1024):h.update(block)
                entries.append({'path':p.relative_to(root).as_posix(),'sha256':h.hexdigest(),'size_bytes':p.stat().st_size})
        (root/'BACKUP-MANIFEST.json').write_text(json.dumps({'format':'shortform-private-backup-v1',
            'contains_secrets':True,'restore_automation_must_remain_off':True,'entries':entries}))
        fd=os.open(partial,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        try:
            with os.fdopen(fd,'wb') as out:
                age=subprocess.Popen(encrypt,stdin=subprocess.PIPE,stdout=out,stderr=subprocess.DEVNULL)
                try:
                    with tarfile.open(fileobj=age.stdin,mode='w|') as archive:
                        for p in sorted(root.rglob('*')):
                            if p.is_file():archive.add(p,arcname=p.relative_to(root).as_posix(),recursive=False)
                    age.stdin.close()
                    if age.wait(timeout=120):raise RuntimeError('backup_encryption_failed')
                finally:
                    if age.poll() is None:age.kill();age.wait()
            partial.replace(output)
        finally:partial.unlink(missing_ok=True)
    return {'state':'ENCRYPTED_BACKUP_CREATED_NOT_RESTORE_TESTED','path':str(output),'contains_secrets':True,'share_publicly':False}


def main():
    p=argparse.ArgumentParser();p.add_argument('--target',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--recipient');p.add_argument('--key-file',type=Path)
    a=p.parse_args();print(json.dumps(backup(a.target,a.output,a.recipient,a.key_file)))


if __name__=='__main__':main()
