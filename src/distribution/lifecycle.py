"""Explicit activation/pause for one installer-owned instance; never restore old DB state."""
from __future__ import annotations

from datetime import datetime,timezone,timedelta
import hmac
import json
from pathlib import Path
import subprocess

from doctor import REQUIRED_CHECKS,native_workflows,no_other_creator
from installer import atomic,command,dotenv
from instance import FLAGS,WORKFLOWS,Instance,JsonObject,SetupError,write_environment
from onboard import native


def accept_receipt(instance: Instance, now: datetime) -> JsonObject:
    receipt=json.loads((instance.root/'doctor-receipt.json').read_text())
    signature=receipt.pop('signature',None)
    if not isinstance(signature,str) or not hmac.compare_digest(signature,instance.sign(receipt)):
        raise SetupError('readiness_receipt_signature_invalid')
    checked=datetime.fromisoformat(receipt['checked_at'])
    if checked.tzinfo is None or not timedelta(0)<=now-checked<=timedelta(minutes=15):
        raise SetupError('readiness_receipt_expired')
    if receipt.get('fingerprint')!=instance.fingerprint():raise SetupError('readiness_receipt_stale')
    checks=receipt.get('checks')
    if (receipt.get('format')!='shortform-readiness-v1' or receipt.get('ready') is not True
            or not isinstance(checks,list) or len(checks)!=len(REQUIRED_CHECKS)
            or {c.get('name') for c in checks if isinstance(c,dict)}!=set(REQUIRED_CHECKS)
            or any(c.get('status')!='PASS' for c in checks)):
        raise SetupError('all_readiness_checks_required')
    return receipt


def busy(instance: Instance) -> bool:
    if any((instance.root/'coordinator-state'/n).exists() for n in ('pending-task.json','pending-media.json')):
        return True
    sql="SELECT (SELECT count(*) FROM generation_jobs WHERE status IN ('queued','running'))+(SELECT count(*) FROM outbox WHERE (event_type='creative_production' AND status='PLANNING') OR (event_type='creative_plan_ready' AND status='MEDIA_RUNNING') OR (event_type='publish_sheet_handoff' AND status='IN_PROGRESS'))"
    result=command(instance.root,instance.state['settings'],'exec','-T','postgres','psql',
        '-h','127.0.0.1','-U','shortform','-d','shortform','-Atc',sql)
    if result.returncode:raise SetupError('idle_state_unavailable')
    return int(result.stdout.strip())!=0


def set_runtime(instance: Instance, enabled: bool) -> None:
    path=instance.root/'creative-runtime.json'
    config=json.loads(path.read_text());config['runtime_enabled']=enabled
    # Bind-mounted file: preserve its inode so an existing process never sees a
    # stale mount after a later recreate. The coordinator is stopped for activation.
    path.write_text(json.dumps(config,ensure_ascii=False,indent=2)+'\n');path.chmod(0o600)


def enable(target: Path, *, consent: bool) -> JsonObject:
    if not consent:raise SetupError('production_consent_required')
    instance=Instance.load(target);c=instance.state['settings']
    if instance.state.get('automation_enabled') is True:
        raise SetupError('already_enabled_use_status_or_pause')
    receipt=accept_receipt(instance,datetime.now(timezone.utc))
    if busy(instance):raise SetupError('active_work_cannot_be_reconfigured')
    if not no_other_creator(instance):raise SetupError('another_local_creator_owns_source_sheet')
    if command(instance.root,c,'stop','coordinator').returncode:
        raise SetupError('coordinator_must_be_idle_and_stopped_before_activation')
    # Existing credentials and workflow IDs stay native. No DB rollback on errors.
    snapshot=instance.root/('activation-before-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')+'.json')
    atomic(snapshot,json.dumps({'environment':dotenv(instance.root/'.env'),
        'state':instance.state,'runtime':json.loads((instance.root/'creative-runtime.json').read_text())},indent=2))
    try:
        for wid in WORKFLOWS:
            native(instance.root,c,'n8n','publish:workflow','--id='+wid)
        if command(instance.root,c,'restart','n8n').returncode:
            raise SetupError('native_scheduler_reload_failed')
        actual=native_workflows(instance)
        if len(actual)!=len(WORKFLOWS) or any(not w.get('activeVersionId') for w in actual):
            raise SetupError('native_published_version_verification_failed')
        anchor=dotenv(instance.root/'.env').get('PRODUCTION_START_AT') or datetime.now(timezone.utc).isoformat()
        write_environment(instance,{**{k:'true' for k in FLAGS},'PRODUCTION_START_AT':anchor})
        if command(instance.root,c,'up','-d','--no-deps','--no-build','gateway','worker').returncode:
            raise SetupError('activation_service_start_failed')
        set_runtime(instance,True)
        instance.save_state(state='ACTIVATING',automation_enabled=True,
            activation_evidence_at=receipt['checked_at'],production_certified=False)
        if command(instance.root,c,'--profile','creative','up','-d','--no-deps','--no-build','coordinator').returncode:
            raise SetupError('activation_coordinator_start_uncertain')
        instance.save_state(state='AUTOMATION_ENABLED',automation_enabled=True,
            activation_evidence_at=receipt['checked_at'],production_certified=False)
    except (OSError,ValueError,RuntimeError,subprocess.SubprocessError) as exc:
        # Stop new production admissions and queue handoffs; keep known job state.
        write_environment(instance,{k:'false' for k in FLAGS})
        set_runtime(instance,False)
        try:can_stop=not busy(instance)
        except (OSError,ValueError,RuntimeError,subprocess.SubprocessError):can_stop=False
        if can_stop:command(instance.root,c,'stop','coordinator')
        command(instance.root,c,'up','-d','--no-deps','--no-build','gateway')
        for wid in WORKFLOWS:
            try:native(instance.root,c,'n8n','unpublish:workflow','--id='+wid)
            except (OSError,ValueError,RuntimeError,subprocess.SubprocessError):
                instance.save_state(state='ACTIVATION_FAILED_MANUAL_NATIVE_DEACTIVATION_REQUIRED',automation_enabled=False)
                raise SetupError('manual_native_workflow_deactivation_required') from exc
        instance.save_state(state='ACTIVATION_FAILED_SAFE_OFF' if can_stop else 'ACTIVATION_FAILED_DRAIN_EXISTING_WORK',automation_enabled=False)
        raise SetupError('activation_failed_new_admissions_off') from exc
    return {'state':'AUTOMATION_ENABLED','workflows_activated':len(actual),
        'daily_story_limit':c['daily_story_limit'],'production_interval_minutes':c['production_interval_minutes'],
        'catch_up_burst':False,'first_complete_episode_verified':False}


def pause(target: Path) -> JsonObject:
    instance=Instance.load(target);c=instance.state['settings']
    write_environment(instance,{k:'false' for k in FLAGS})
    if command(instance.root,c,'up','-d','--no-deps','--no-build','gateway').returncode:
        raise SetupError('pause_gateway_reload_required')
    active=busy(instance)
    if not active and (instance.root/'creative-runtime.json').exists():
        set_runtime(instance,False)
        if command(instance.root,c,'stop','coordinator').returncode:
            raise SetupError('pause_coordinator_stop_required')
    for workflow in native_workflows(instance):
        native(instance.root,c,'n8n','unpublish:workflow','--id='+workflow['id'])
    if command(instance.root,c,'restart','n8n').returncode:
        raise SetupError('pause_native_scheduler_reload_required')
    state='DRAINING_EXISTING_WORK_NEW_ADMISSIONS_OFF' if active else 'PAUSED'
    instance.save_state(state=state,automation_enabled=False)
    return {'state':state,'paid_jobs_killed':False,'native_workflow_ids_preserved':True,
            'follow_up':'Run pause again after existing work completes.' if active else None}
