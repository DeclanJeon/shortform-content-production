"""Map this installation's OWN Google account ID; import creator workflows INACTIVE.
Native Google OAuth consent is completed in n8n first. This command never activates,
publishes to YouTube, generates media, or claims scopes were verified.
"""
import argparse
import fcntl
import hashlib
import json
from pathlib import Path
import subprocess
import sys
sys.dont_write_bytecode=True
from installer import atomic,command,dotenv,preflight,settings,verify_source
from workflows import render

ACCOUNT_FIELDS={'source_sheet_id','publish_sheet_id','publish_queue_tab','publish_privacy',
    'publish_category_id','archive_folder_id','typecast_voice_id','google_credential_id'}


def native(target,c,*args,input=None,timeout=120):
    ids=command(target,c,'ps','-q','n8n').stdout.split()
    if len(ids)!=1:raise ValueError('n8n_not_running')
    argv=['docker','exec','-i','-u','node','-e','N8N_RUNNERS_BROKER_PORT=5685',ids[0],*args]
    p=subprocess.run(argv,input=input,text=True,capture_output=True,timeout=timeout)
    if p.returncode:raise RuntimeError('native_n8n_command_failed')
    return p.stdout


def put_native(target,c,path,value):
    native(target,c,'node','-e',"require('fs').writeFileSync(process.argv[1],require('fs').readFileSync(0),{mode:384})",path,input=json.dumps(value))


def onboard(target,accounts):
    target=Path(target).absolute()
    if any(p.is_symlink() for p in (target,*target.parents)):raise ValueError('unsafe_install_directory')
    before=json.loads((target/'install-state.json').read_text());source=target/'source'
    if before.get('format')!='shortform-install-v1' or before.get('automation_enabled') is not False:raise ValueError('inactive_installer_instance_required')
    if verify_source(source)!=before['source_manifest_sha256']:raise ValueError('installed_source_changed')
    data=json.loads(Path(accounts).read_text())
    if not isinstance(data,dict) or set(data)-ACCOUNT_FIELDS:raise ValueError('accounts_file_only_accepts_ids_no_secrets')
    with open(target/'.onboard.lock','a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        current={**before['settings'],**data};preflight(target,current)
        conf=command(target,current,'config','--format','json')
        if conf.returncode:raise ValueError('compose_configuration_invalid')
        env=json.loads(conf.stdout)['services']['gateway']['environment']
        if any(str(env.get(k)).lower()!='false' for k in ('AUTOMATIC_CREATIVE_ENABLED','AUTOMATIC_MEDIA_ENABLED','AUTOMATIC_POLICY_APPROVAL_ENABLED')):raise ValueError('onboarding_requires_all_automation_off')
        probe=target/'.account-settings.json';atomic(probe,json.dumps(current))
        try:c=settings(probe)
        finally:probe.unlink()
        workflows=render(source,c)
        if len(workflows)!=6:raise ValueError('creator_workflow_set_incomplete')
        path='/tmp/shortform-account-metadata.json'
        try:
            native(target,c,'n8n','export:credentials','--all','--output='+path)
            meta=json.loads(native(target,c,'node','-e',"const a=JSON.parse(require('fs').readFileSync(process.argv[1],'utf8'));console.log(JSON.stringify(a.map(x=>({id:x.id,type:x.type}))))",path))
            types={x['id']:x['type'] for x in meta}
            if types.get(c['google_credential_id'])!='googleDriveOAuth2Api':raise ValueError('native_google_credential_not_found')
            if 'sf-gateway-api' not in types:
                credential=[{'id':'sf-gateway-api','name':'This installation gateway','type':'httpHeaderAuth','data':{'name':'Authorization','value':'Bearer '+dotenv(target/'.env')['API_TOKEN']}}]
                put_native(target,c,path,credential);native(target,c,'n8n','import:credentials','--input='+path)
            elif types['sf-gateway-api']!='httpHeaderAuth':raise ValueError('gateway_credential_id_collision')
            if 'sf-review-hmac' not in types:
                secret=command(target,c,'exec','-T','gateway','python','-c',
                    "from pathlib import Path;print(Path('/run/secrets/review_callback_secret').read_text().strip())")
                if secret.returncode or not secret.stdout.strip():raise ValueError('instance_signing_secret_unavailable')
                put_native(target,c,path,[{'id':'sf-review-hmac','name':'This installation review signer',
                    'type':'crypto','data':{'hmacSecret':secret.stdout.strip()}}])
                native(target,c,'n8n','import:credentials','--input='+path)
            elif types['sf-review-hmac']!='crypto':raise ValueError('review_signer_credential_id_collision')
            # Back up only app-owned configuration before changes; native OAuth stays native.
            backup=target/'onboarding-before';backup.mkdir(exist_ok=True,mode=0o700)
            for name in ('.env','install-state.json'):
                saved=backup/name
                if not saved.exists():saved.write_bytes((target/name).read_bytes());saved.chmod(0o600)
            put_native(target,c,path,workflows);native(target,c,'n8n','import:workflow','--input='+path)
        finally:
            native(target,c,'node','-e',"require('fs').rmSync(process.argv[1],{force:true})",path)
        for w in workflows:atomic(target/'workflows'/(w['id']+'.json'),json.dumps(w,ensure_ascii=False,indent=2),0o644)
        env=dotenv(target/'.env');env.update(SOURCE_SHEET_ID=c['source_sheet_id'],
            PUBLISH_SHEET_ID=c['publish_sheet_id'],PUBLISH_QUEUE_TAB=c['publish_queue_tab'],
            PUBLISH_PRIVACY=c['publish_privacy'],PUBLISH_CATEGORY_ID=str(c['publish_category_id']),
            AUTOMATIC_ARCHIVE_FOLDER_ID=c['archive_folder_id'],TYPECAST_VOICE_ID=c['typecast_voice_id'])
        atomic(target/'.env',''.join(k+'='+v+'\n' for k,v in env.items()))
        before.update(settings=c,state='ACCOUNTS_MAPPED_WORKFLOWS_IMPORTED_INACTIVE',automation_enabled=False,production_certified=False)
        atomic(target/'install-state.json',json.dumps(before,indent=2))
        if command(target,c,'up','-d','--no-deps','--no-build','gateway','worker').returncode:raise RuntimeError('account_environment_recreate_required')
        result={'state':before['state'],'workflows_imported':len(workflows),'automation_enabled':False,
            'publisher_workflow_imported':False,'youtube_credential_required':False,
            'provider_oauth_live_verified':False,
            'remaining_gates':['live_google_scopes_source_and_publish_queue_checks','licensed_external_creative_runtime',
                'n8n_signing_runtime','typecast_configuration','publisher_sheet_queue_initialization',
                'activation_and_update_rollback_validation']}
        atomic(target/'onboarding-receipt.json',json.dumps(result,indent=2));return result


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--target',type=Path,required=True);p.add_argument('--accounts',type=Path,required=True);a=p.parse_args()
    try:print(json.dumps(onboard(a.target,a.accounts)))
    except (OSError,ValueError,RuntimeError,subprocess.SubprocessError):print(json.dumps({'state':'ONBOARDING_STOPPED_AUTOMATION_OFF'}));raise SystemExit(1)

if __name__=='__main__':main()
