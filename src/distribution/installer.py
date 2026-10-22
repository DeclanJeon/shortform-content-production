"""Account-neutral, fail-closed infrastructure installer (Linux/Compose v2)."""
from __future__ import annotations
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import time
from urllib.request import urlopen

SCHEMA={'project','n8n_port','source_sheet_id','publish_sheet_id','publish_queue_tab','publish_privacy',
        'publish_category_id','archive_folder_id','typecast_voice_id','google_credential_id',
        'daily_story_limit','production_interval_minutes','allow_sheet_inspiration'}
DEFAULT={'project':'shortform-content','n8n_port':15680,'source_sheet_id':'','publish_sheet_id':'',
         'publish_queue_tab':'발행 대기','publish_privacy':'공개','publish_category_id':22,
         'archive_folder_id':'','typecast_voice_id':'','google_credential_id':'',
         'daily_story_limit':12,'production_interval_minutes':120,'allow_sheet_inspiration':False}


def settings(path):
    data=json.loads(Path(path).read_text())
    if not isinstance(data,dict) or set(data)-SCHEMA:raise ValueError('unsupported_configuration_field')
    c={**DEFAULT,**data}
    if not c['publish_sheet_id']:c['publish_sheet_id']=c['source_sheet_id']
    if not re.fullmatch(r'[a-z][a-z0-9_-]{2,39}',c['project']):raise ValueError('invalid_project_name')
    if type(c['n8n_port']) is not int or not 1024<=c['n8n_port']<=65535:raise ValueError('invalid_n8n_port')
    if type(c['daily_story_limit']) is not int or not 1<=c['daily_story_limit']<=12:raise ValueError('invalid_daily_story_limit')
    interval=c['production_interval_minutes']
    if type(interval) is not int or interval<120 or 1440%interval:raise ValueError('invalid_production_interval')
    if type(c['publish_category_id']) is not int or not 1<=c['publish_category_id']<=9999:raise ValueError('invalid_publish_category_id')
    if type(c['allow_sheet_inspiration']) is not bool:raise ValueError('invalid_sheet_inspiration_switch')
    if c['publish_privacy'] not in ('공개','비공개','비공개목록'):raise ValueError('invalid_publish_privacy')
    if (not isinstance(c['publish_queue_tab'],str) or not c['publish_queue_tab'].strip()
            or len(c['publish_queue_tab'])>100 or any(ord(ch)<32 for ch in c['publish_queue_tab'])
            or any(ch in c['publish_queue_tab'] for ch in ('!','/','\\\\'))):
        raise ValueError('invalid_publish_queue_tab')
    for key in SCHEMA-{'project','n8n_port','publish_queue_tab','publish_privacy','publish_category_id',
                       'daily_story_limit','production_interval_minutes','allow_sheet_inspiration'}:
        value=c[key]
        if not isinstance(value,str) or (value and not re.fullmatch('[A-Za-z0-9_-]{1,200}',value)):raise ValueError('invalid_'+key)
    for key in ('source_sheet_id','publish_sheet_id'):
        if c[key] and not re.fullmatch(r'[A-Za-z0-9_-]{10,120}',c[key]):raise ValueError('invalid_'+key)
    if c['archive_folder_id'] and not re.fullmatch(r'[A-Za-z0-9_-]{10,200}',c['archive_folder_id']):
        raise ValueError('invalid_archive_folder_id')
    return c


def verify_source(root):
    root=Path(root).resolve(strict=True)
    manifest=json.loads((root/'MANIFEST.json').read_text())
    entries=manifest.get('entries',[])
    if manifest.get('format')!='shortform-source-v1' or not entries:raise ValueError('invalid_source_manifest')
    seen=set()
    for entry in entries:
        name=entry['path'];p=root/name
        if name in seen or Path(name).is_absolute() or '..' in Path(name).parts or '\\' in name:raise ValueError('unsafe_manifest_path')
        seen.add(name)
        if any(x.is_symlink() for x in (p,*p.parents)) or not p.resolve().is_relative_to(root):raise ValueError('source_link_forbidden')
        raw=p.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=entry['sha256'] or len(raw)!=entry['size_bytes']:raise ValueError('source_checksum_mismatch')
    all_paths=list(root.rglob('*'))
    if any(p.is_symlink() for p in all_paths):raise ValueError('source_link_forbidden')
    actual={p.relative_to(root).as_posix() for p in all_paths if p.is_file()}
    if actual!=seen|{'MANIFEST.json'}:raise ValueError('unexpected_source_file')
    return hashlib.sha256((root/'MANIFEST.json').read_bytes()).hexdigest()


def atomic(path,data,mode=0o600):
    temporary=path.with_name(path.name+'.new')
    fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,mode)
    try:
        with os.fdopen(fd,'w') as f:f.write(data);f.flush();os.fsync(f.fileno())
        os.replace(temporary,path)
    finally:temporary.unlink(missing_ok=True)


def dotenv(path):
    values={}
    if path.exists():
        for line in path.read_text().splitlines():
            if line and not line.startswith('#'):
                key,value=line.split('=',1);values[key]=value
    return values


def prepare(source,target,c):
    source=Path(source).resolve(strict=True);target=Path(target).absolute()
    if any(p.is_symlink() for p in (target,*target.parents)):raise ValueError('unsafe_install_directory')
    if target==source or target.is_relative_to(source):raise ValueError('install_directory_must_be_outside_source')
    fingerprint=verify_source(source)
    existed=target.exists()
    if existed and not (target/'install-state.json').exists():raise ValueError('target_not_managed_by_installer')
    target.mkdir(parents=True,exist_ok=True,mode=0o700);target.chmod(0o700)
    if existed:
        state=json.loads((target/'install-state.json').read_text())
        if state['source_manifest_sha256']!=fingerprint or state['settings']!=c:raise ValueError('existing_install_requires_explicit_migration')
        if verify_source(target/'source')!=fingerprint:raise ValueError('installed_source_changed')
        env=dotenv(target/'.env')
        if not all(env.get(k) for k in ('POSTGRES_PASSWORD','API_TOKEN','WORKER_TOKEN','N8N_ENCRYPTION_KEY')):
            raise ValueError('existing_secrets_missing_no_automatic_rotation')
        return state
    # Claim the destination before any secret writes, so partial setup is visible.
    state={'format':'shortform-install-v1','state':'PREPARING','source_manifest_sha256':fingerprint,
           'settings':c,'automation_enabled':False,'production_certified':False}
    atomic(target/'install-state.json',json.dumps(state,indent=2)+'\n')
    try:
        shutil.copytree(source,target/'source')
        shutil.copyfile(source/'distribution/compose.yml',target/'compose.yml')
        (target/'secrets').mkdir(mode=0o700)
        # Parent instance stays 0700; the container's n8n user must read mounted workflows.
        (target/'workflows').mkdir(mode=0o755)
        values={k:secrets.token_urlsafe(48) for k in ('POSTGRES_PASSWORD','API_TOKEN','WORKER_TOKEN','N8N_ENCRYPTION_KEY')}
        values.update(N8N_PORT=str(c['n8n_port']),SOURCE_SHEET_ID=c['source_sheet_id'],
                      PUBLISH_SHEET_ID=c['publish_sheet_id'],PUBLISH_QUEUE_TAB=c['publish_queue_tab'],
                      PUBLISH_PRIVACY=c['publish_privacy'],PUBLISH_CATEGORY_ID=str(c['publish_category_id']),
                      AUTOMATIC_ARCHIVE_FOLDER_ID=c['archive_folder_id'],TYPECAST_VOICE_ID=c['typecast_voice_id'])
        values.update(AUTOMATIC_CREATIVE_DAILY_LIMIT=str(c['daily_story_limit']),PRODUCTION_INTERVAL_MINUTES=str(c['production_interval_minutes']),
                      ALLOW_SHEET_INSPIRATION=str(c['allow_sheet_inspiration']).lower(),IMAGE_DAILY_RESERVATION_USD=str(10*c['daily_story_limit']),
                      IMAGE_DAILY_CALL_LIMIT=str(40*c['daily_story_limit']),MAX_TTS_REQUESTS=str(2*c['daily_story_limit']))
        atomic(target/'.env',''.join(k+'='+v+'\n' for k,v in values.items()))
        atomic(target/'secrets/review_callback_secret',secrets.token_hex(32))
        # Empty means TTS unavailable. Never copy a developer's provider credentials.
        atomic(target/'secrets/typecast_api_key','')
        state['state']='PREPARED';atomic(target/'install-state.json',json.dumps(state,indent=2)+'\n')
        return state
    except Exception:
        # Preserve partial files, never delete or overwrite possibly-created data.
        raise RuntimeError('partial_install_preserved_for_recovery') from None


def command(target,c,*args,timeout=600):
    files=['-f',str(target/'compose.yml')]
    runtime=target/'runtime-compose.json'
    if runtime.exists():files+=['-f',str(runtime)]
    return subprocess.run(['docker','compose','--project-name',c['project'],'--env-file',str(target/'.env'),
        *files,*args],cwd=target,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
        text=True,timeout=timeout,check=False)


def preflight(target,c,*,require_new_resources=False):
    if not sys.platform.startswith('linux'):raise ValueError('linux_required_for_this_preview')
    for argv in (['docker','version','--format','{{.Server.Version}}'],['docker','compose','version']):
        p=subprocess.run(argv,capture_output=True,text=True,timeout=20)
        if p.returncode:raise ValueError('docker_compose_v2_required')
    p=subprocess.run(['docker','ps','-a','--filter','label=com.docker.compose.project='+c['project'],
        '--format','{{.Label "com.docker.compose.project.working_dir"}}'],capture_output=True,text=True,timeout=20)
    if p.returncode:raise ValueError('docker_inventory_unavailable')
    if any(Path(line).resolve()!=target.resolve() for line in p.stdout.splitlines() if line):raise ValueError('compose_project_already_used_elsewhere')
    if require_new_resources or not (target/'install-state.json').exists():
        # Containers can be gone while their data volumes remain. Include known
        # default names as well as Compose labels (also catches unlabeled volumes).
        for kind,names in (('volume',{c['project']+'_'+n for n in ('database','artifacts','automation')}),
                           ('network',{c['project']+'_default'})):
            listed=subprocess.run(['docker',kind,'ls','--format','{{.Name}}'],capture_output=True,text=True,timeout=20)
            labeled=subprocess.run(['docker',kind,'ls','--filter','label=com.docker.compose.project='+c['project'],'--format','{{.Name}}'],capture_output=True,text=True,timeout=20)
            if listed.returncode or labeled.returncode:raise ValueError('docker_inventory_unavailable')
            if names.intersection(listed.stdout.splitlines()) or labeled.stdout.strip():
                raise ValueError('compose_project_resources_already_used')


def start(target,c):
    existing=json.loads((target/'install-state.json').read_text())
    if existing.get('automation_enabled') is True:
        raise ValueError('pause_before_install_or_update')
    print(json.dumps({'step':'DOCKER_PREFLIGHT'}),flush=True)
    preflight(target,c)
    if command(target,c,'config','--quiet').returncode:raise ValueError('compose_configuration_invalid')
    print(json.dumps({'step':'BUILDING_CONTAINERS'}),flush=True)
    if command(target,c,'build',timeout=1800).returncode:raise RuntimeError('container_build_failed_details_not_printed')
    # Only these two application-owned files; no host-wide chmod/chown.
    script="import os;[(os.chown('/setup/'+n,10001,10001),os.chmod('/setup/'+n,0o400)) for n in ('typecast_api_key','review_callback_secret')]"
    p=command(target,c,'run','--rm','--no-deps','--user','0:0','-v',str(target/'secrets')+':/setup',
              'worker','python','-c',script)
    if p.returncode:raise RuntimeError('application_secret_permissions_failed')
    print(json.dumps({'step':'INITIALIZING_DATABASE'}),flush=True)
    if command(target,c,'up','-d','--no-build','postgres').returncode:raise RuntimeError('database_start_failed')
    tables=None
    for _ in range(30):
        check=command(target,c,'exec','-T','postgres','psql','-h','127.0.0.1','-U','shortform','-d','shortform','-Atc',
            "SELECT count(*) FROM pg_tables WHERE schemaname='public'",timeout=10)
        if check.returncode==0:tables=int(check.stdout.strip());break
        time.sleep(2)
    if tables is None:raise RuntimeError('database_start_timeout')
    if tables==0:
        state=json.loads((target/'install-state.json').read_text())
        if state['state']!='PREPARED':raise RuntimeError('existing_database_empty_no_automatic_reinitialization')
        argv=['docker','compose','--project-name',c['project'],'--env-file',str(target/'.env'),'-f',str(target/'compose.yml'),
              'exec','-T','postgres','psql','-h','127.0.0.1','-U','shortform','-d','shortform','-v','ON_ERROR_STOP=1','--single-transaction']
        initialized=subprocess.run(argv,cwd=target,input=(target/'source/db/schema.sql').read_text(),
            text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=60)
        if initialized.returncode:raise RuntimeError('database_schema_initialization_failed')
    # Do not call health READY merely because generic outbox/job tables exist.
    # Old/partial databases are held, never silently migrated by installation.
    check=command(target,c,'exec','-T','postgres','psql','-h','127.0.0.1','-U','shortform','-d','shortform','-Atc',
        "SELECT to_regclass('public.generation_jobs') IS NOT NULL AND to_regclass('public.outbox') IS NOT NULL "
        "AND to_regclass('public.review_callback_events') IS NOT NULL "
        "AND (SELECT count(*) FROM information_schema.columns WHERE table_schema='public' AND table_name='review_callback_events' "
        "AND column_name IN ('event_id','nonce','review_id','decision','signature_timestamp','payload_sha256','received_at'))=7 "
        "AND (SELECT count(DISTINCT a.attname) FROM pg_index i "
        "JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=i.indkey[0] "
        "WHERE i.indrelid=to_regclass('public.review_callback_events') "
        "AND i.indisunique AND i.indisvalid AND i.indisready AND i.indislive AND i.indimmediate "
        "AND i.indpred IS NULL AND i.indexprs IS NULL AND i.indnkeyatts=1 "
        "AND a.attname IN ('event_id','nonce'))=2")
    if check.returncode or check.stdout.strip()!='t':raise RuntimeError('unexpected_existing_database_schema')
    print(json.dumps({'step':'STARTING_SERVICES'}),flush=True)
    if command(target,c,'up','-d','--no-build').returncode:raise RuntimeError('services_start_failed')
    print(json.dumps({'step':'WAITING_FOR_GATEWAY_HEALTH'}),flush=True)
    for _ in range(30):
        p=command(target,c,'exec','-T','gateway','python','-c',
            "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8080/v1/healthz',timeout=3)",timeout=10)
        if p.returncode==0:break
        time.sleep(2)
    else:raise RuntimeError('gateway_health_check_failed')
    for _ in range(30):
        try:
            with urlopen('http://127.0.0.1:'+str(c['n8n_port'])+'/healthz',timeout=3) as response:
                if response.status==200:break
        except OSError:pass
        time.sleep(2)
    else:raise RuntimeError('n8n_health_check_failed')
    for service in ('postgres','gateway','worker','n8n'):
        ids=command(target,c,'ps','-q',service).stdout.split()
        if len(ids)!=1:raise RuntimeError('service_not_running')
        check=subprocess.run(['docker','inspect','--format','{{.State.Running}}',ids[0]],capture_output=True,text=True,timeout=10)
        if check.returncode or check.stdout.strip()!='true':raise RuntimeError('service_not_running')
    state=json.loads((target/'install-state.json').read_text());state['state']='INFRASTRUCTURE_READY_AWAITING_ACCOUNTS'
    atomic(target/'install-state.json',json.dumps(state,indent=2)+'\n')
    return {'state':state['state'],'n8n_url':'http://localhost:'+str(c['n8n_port']),
            'automation_enabled':False,'production_certified':False}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['prepare','install','status'])
    p.add_argument('--config',type=Path,required=True);p.add_argument('--target',type=Path,required=True)
    a=p.parse_args();c=settings(a.config);source=Path(__file__).resolve().parents[1];target=a.target.absolute()
    if a.action=='status':
        print(json.dumps(json.loads((target/'install-state.json').read_text())));return
    # A different project path is not automatically an authorization to start Docker services.
    if a.action=='install':preflight(target,c)
    target.parent.mkdir(parents=True,exist_ok=True)
    with open(target.parent/('.'+target.name+'.install.lock'),'a') as lock:
        os.chmod(lock.name,0o600);fcntl.flock(lock,fcntl.LOCK_EX)
        state=prepare(source,target,c)
        if state['state']=='PREPARING':raise RuntimeError('partial_install_requires_recovery')
        print(json.dumps(start(target,c) if a.action=='install' else {'state':state['state'],'automation_enabled':False}))


if __name__=='__main__':
    try:main()
    except (ValueError,RuntimeError,OSError,subprocess.SubprocessError):
        # No raw subprocess stderr, .env values, or exception text in console output.
        error=sys.exc_info()[1];code=str(error)
        reason=code if re.fullmatch(r'[a-z][a-z0-9_]{0,90}',code) else 'installer_io_or_subprocess_failed'
        print(json.dumps({'state':'INSTALLATION_STOPPED','reason':reason,'automation_enabled':False}),file=sys.stderr)
        raise SystemExit(1)
