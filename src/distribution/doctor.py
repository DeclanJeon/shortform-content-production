"""No-generation readiness checks with signed, short-lived activation evidence."""
from __future__ import annotations

import ast
from dataclasses import dataclass
from datetime import datetime,timezone
import hashlib
import hmac
import json
from pathlib import Path
import secrets
import subprocess
from typing import Callable,assert_never

from installer import atomic,command,dotenv
from instance import Instance,Json,JsonObject,SetupError,WORKFLOWS
from onboard import native,put_native
from preflight_workflow import build


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    passed: bool
    scope: str


REQUIRED_CHECKS=('stack_health','runtime_auth','image_executor_smoke','tts_configuration',
                 'own_accounts_and_signer','workflows_imported','single_creator')


def headers(source: Path) -> tuple[list[str],list[str]]:
    """Read literal schema data without importing the API's optional dependencies."""
    found={}
    for node in ast.parse((source/'api/sheet_intake.py').read_text()).body:
        match node:
            case ast.Assign(targets=[ast.Name(id=name)],value=value) if name in ('DASHBOARD_HEADERS','SUMMARY_HEADERS'):
                found[name]=ast.literal_eval(value)
    if set(found)!={'DASHBOARD_HEADERS','SUMMARY_HEADERS'}:
        raise SetupError('sheet_header_schema_unavailable')
    return found['DASHBOARD_HEADERS'],found['SUMMARY_HEADERS']


def review_key(instance: Instance) -> str:
    result=command(instance.root,instance.state['settings'],'exec','-T','gateway','python','-c',
        "from pathlib import Path;print(Path('/run/secrets/review_callback_secret').read_text().strip())")
    if result.returncode or not result.stdout.strip():raise SetupError('signer_key_unavailable')
    return result.stdout.strip()


def native_workflows(instance: Instance) -> list[JsonObject]:
    script="const {DatabaseSync}=require('node:sqlite');const d=new DatabaseSync('/home/node/.n8n/database.sqlite',{readOnly:true});console.log(JSON.stringify(d.prepare('SELECT id,active,activeVersionId FROM workflow_entity WHERE id IN (?,?,?,?,?,?)').all(...JSON.parse(process.argv[1]))));d.close();"
    return json.loads(native(instance.root,instance.state['settings'],'node','-e',script,json.dumps(WORKFLOWS)))


def execution_evidence(output: str, challenge: str, digest: str) -> bool:
    """Accept the actual native signer result, not a generic CLI success string."""
    marker='Execution was successful:'
    raw=output.rsplit(marker,1)[1] if marker in output else output
    start=raw.find('{')
    if start<0:return False
    try:value,_=json.JSONDecoder().raw_decode(raw[start:])
    except json.JSONDecodeError:return False
    if isinstance(value,dict) and isinstance(value.get('data'),dict):
        result=value['data'].get('resultData')
        if isinstance(result,dict) and result.get('error'):return False
    def match_result(node: Json) -> bool:
        match node:
            case dict():
                if (node.get('status')=='OWN_GOOGLE_RESOURCES_AND_QUEUE_VERIFIED' and node.get('challenge')==challenge
                        and isinstance(node.get('signer_digest'),str)
                        and hmac.compare_digest(node['signer_digest'],digest)):
                    return True
                return any(match_result(x) for x in node.values())
            case list():return any(match_result(x) for x in node)
            case str() | int() | float() | bool() | None:return False
            case unexpected:assert_never(unexpected)
    return match_result(value)


def check_accounts(instance: Instance) -> bool:
    c=instance.state['settings'];challenge=secrets.token_hex(24)
    dashboard,summary=headers(instance.root/'source')
    workflow=build(c,challenge,dashboard,summary)
    path='/tmp/shortform-readonly-check.json'
    try:
        put_native(instance.root,c,path,[workflow])
        native(instance.root,c,'n8n','import:workflow','--input='+path)
        output=native(instance.root,c,'n8n','execute','--id=SFSetupReadOnlyCheck','--rawOutput',timeout=240)
    finally:
        native(instance.root,c,'node','-e',"require('fs').rmSync(process.argv[1],{force:true})",path)
    digest=hmac.new(review_key(instance).encode(),challenge.encode(),hashlib.sha256).hexdigest()
    return execution_evidence(output,challenge,digest)


def no_other_creator(instance: Instance) -> bool:
    """Fail closed when another active local producer has an unknown source sheet."""
    ids=subprocess.run(['docker','ps','-q'],capture_output=True,text=True,timeout=20,check=True).stdout.split()
    if not ids:return True
    entries=json.loads(subprocess.run(['docker','inspect',*ids],capture_output=True,text=True,timeout=20,check=True).stdout)
    for entry in entries:
        config=entry['Config'];env=dict(x.split('=',1) for x in config.get('Env',[]) if '=' in x)
        own=config.get('Labels',{}).get('com.docker.compose.project')==instance.state['settings']['project']
        if own or env.get('AUTOMATIC_CREATIVE_ENABLED','false').lower()!='true':
            continue
        configured_source=instance.state['settings']['source_sheet_id']
        other_source=env.get('SOURCE_SHEET_ID','')
        if not configured_source or not other_source or other_source==configured_source:
            return False
    return True


def diagnose(target: Path) -> JsonObject:
    instance=Instance.load(target);c=instance.state['settings'];root=instance.root
    checks=[]
    def check(name: str, scope: str, run: Callable[[],bool]) -> None:
        try:passed=bool(run())
        except (OSError,ValueError,RuntimeError,subprocess.SubprocessError):passed=False
        checks.append(Check(name,passed,scope))
    def stack() -> bool:
        for service in ('postgres','gateway','worker','n8n'):
            ids=command(root,c,'ps','-q',service).stdout.split()
            if len(ids)!=1:return False
            result=subprocess.run(['docker','inspect','--format','{{json .State}}',ids[0]],capture_output=True,text=True,timeout=15,check=True)
            state=json.loads(result.stdout)
            if state.get('Running') is not True or state.get('Paused') is not False:return False
        return command(root,c,'exec','-T','gateway','python','-c',
            "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8080/v1/healthz',timeout=5)").returncode==0
    def auth() -> bool:
        probe="from creative.skills import load_bundle;print(load_bundle('/skills','/repository')['revision'])"
        actual=command(root,c,'run','--rm','--no-deps','-T','--entrypoint','python','coordinator','-c',probe,timeout=60)
        if actual.returncode or actual.stdout.strip()!=dotenv(root/'.env').get('CREATIVE_SKILL_REVISION'):return False
        config=json.loads((root/'creative-runtime.json').read_text())
        for model in dict.fromkeys(config[k] for k in ('model','planning_reviewer_model','vision_model')):
            result=command(root,c,'run','--rm','--no-deps','-T','--entrypoint','pi','coordinator',
                'auth','check','--provider',config['provider'],'--model',model,'--json',timeout=60)
            if result.returncode or json.loads(result.stdout).get('status')!='ready':return False
        return True
    def image() -> bool:
        config=json.loads((root/'creative-runtime.json').read_text())
        return command(root,c,'run','--rm','--no-deps','-T','--entrypoint','node','coordinator',
            config['codex_imagen_script'],'--smoke',timeout=60).returncode==0
    def tts() -> bool:
        if not c.get('typecast_voice_id'):return False
        result=command(root,c,'exec','-T','worker','python','-c',
            "from pathlib import Path;print(bool(Path('/run/secrets/typecast_api_key').read_text().strip()))")
        return result.returncode==0 and result.stdout.strip()=='True'
    check('stack_health','Application-owned core services and real gateway health endpoint',stack)
    check('runtime_auth','Native Pi readiness; not a media generation or quality guarantee',auth)
    check('image_executor_smoke','User-provided compatible executor smoke mode; no image submission',image)
    check('tts_configuration','Voice ID and key presence only; provider authentication is NOT asserted',tts)
    check('workflows_imported','All six owned workflow IDs exist in native SQLite',
          lambda:{w['id'] for w in native_workflows(instance)}==set(WORKFLOWS))
    check('own_accounts_and_signer','Actual read-only Google source/queue/folder checks and native HMAC challenge',lambda:check_accounts(instance))
    check('single_creator','No other active local Compose project consuming the same source sheet',lambda:no_other_creator(instance))
    ready=all(c.passed for c in checks)
    body={'format':'shortform-readiness-v1','checked_at':datetime.now(timezone.utc).isoformat(),
        'fingerprint':instance.fingerprint(),'ready':ready,
        'checks':[{'name':c.name,'status':'PASS' if c.passed else 'FAIL','scope':c.scope} for c in checks],
        'paid_media_requests':0,'end_to_end_production_verified':False}
    receipt={**body,'signature':instance.sign(body)}
    atomic(root/'doctor-receipt.json',json.dumps(receipt,ensure_ascii=False,indent=2)+'\n')
    return {'state':'READY_FOR_EXPLICIT_ACTIVATION' if ready else 'SETUP_INCOMPLETE',
            'ready':ready,'checks':body['checks'],'automation_changed':False,'paid_media_requests':0}
