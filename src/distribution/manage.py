#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
# ─── How to run ───
# Optional uv: curl -LsSf https://astral.sh/uv/install.sh | sh
# uv run manage.py --help
# Dependency-free alternative: python3 -B manage.py --help
# Repository users: ./install.sh or ./shortform --help
# ──────────────────
"""Portable Shortform setup/control; account consent and paid activation are separate."""
from __future__ import annotations

import argparse
import csv
from enum import StrEnum
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import assert_never
sys.dont_write_bytecode=True

from installer import DEFAULT,atomic,prepare,settings,start,command,preflight
from instance import Instance,JsonObject,SetupError


class Action(StrEnum):
    WIZARD='wizard'
    SETUP='setup'
    ACCOUNTS='accounts'
    RUNTIME='runtime'
    DOCTOR='doctor'
    ENABLE='enable'
    PAUSE='pause'
    STATUS='status'
    TEMPLATES='sheet-templates'
    BACKUP='backup'
    RESTORE='restore'


def apply_runtime(target: Path, spec: Path, key_file: Path | None) -> JsonObject:
    from runtime import configure_runtime
    result=configure_runtime(target,spec)
    instance=Instance.load(target);c=instance.state['settings']
    if key_file:
        if key_file.is_symlink() or key_file.stat().st_mode&0o077 or key_file.stat().st_uid!=os.getuid():
            raise SetupError('typecast_key_file_must_be_private_and_owned')
        value=key_file.read_text().strip()
        if not value or len(value)>4096:raise SetupError('typecast_key_file_invalid')
        atomic(target/'secrets/typecast_api_key',value+'\n')
        script="import os;os.chown('/setup/typecast_api_key',10001,10001);os.chmod('/setup/typecast_api_key',0o400)"
        if command(target,c,'run','--rm','--no-deps','--user','0:0','-v',str(target/'secrets')+':/setup','worker','python','-c',script).returncode:
            raise SetupError('typecast_secret_permissions_failed')
    if command(target,c,'build','coordinator',timeout=1800).returncode:
        raise SetupError('coordinator_build_failed')
    if command(target,c,'up','-d','--no-deps','--no-build','--force-recreate','gateway','worker').returncode:
        raise SetupError('runtime_core_recreate_failed')
    return result


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=list(Action))
    parser.add_argument('--target',type=Path,default=Path.home()/'.local/share/shortform-content-production')
    parser.add_argument('--config',type=Path)
    parser.add_argument('--accounts',type=Path)
    parser.add_argument('--spec',type=Path)
    parser.add_argument('--typecast-key-file',type=Path)
    parser.add_argument('--allow-paid-generation',action='store_true')
    parser.add_argument('--output',type=Path)
    parser.add_argument('--key-file',type=Path)
    parser.add_argument('--backup',type=Path)
    parser.add_argument('--project')
    parser.add_argument('--n8n-port',type=int)
    args=parser.parse_args();action=Action(args.action);target=args.target.expanduser().absolute()
    source=Path(__file__).resolve().parents[1]
    if source.name=='src' and target.is_relative_to(source.parent):
        raise SetupError('instance_must_be_outside_git_checkout')
    if action==Action.WIZARD:
        from wizard import wizard
        print(json.dumps(wizard(source,target,args.config),ensure_ascii=False))
        return
    if action==Action.SETUP and not args.config:
        config=Path.home()/'.config/shortform-studio/settings.json'
        if not config.exists():
            config.parent.mkdir(parents=True,mode=0o700,exist_ok=True)
            atomic(config,json.dumps({**DEFAULT,'project':'shortform-content','n8n_port':15680},indent=2)+'\n')
        args.config=config
    if action==Action.RESTORE:
        from restore import restore
        if not args.backup or not args.key_file or not args.project or not args.n8n_port:
            raise SetupError('restore_requires_backup_key_new_project_and_port')
        print(json.dumps(restore(args.backup,target,args.project,args.n8n_port,key_file=args.key_file)))
        return
    if any(p.is_symlink() for p in (target,*target.parents)):
        raise SetupError('unsafe_install_directory')
    target.parent.mkdir(parents=True,exist_ok=True)
    with (target.parent/('.'+target.name+'.manage.lock')).open('a') as lock:
        os.chmod(lock.name,0o600);fcntl.flock(lock,fcntl.LOCK_EX)
        match action:
            case Action.WIZARD:
                raise SetupError('wizard_must_run_outside_stage_lock')
            case Action.SETUP:
                c=settings(args.config);preflight(target,c);prepare(source,target,c);result=start(target,c)
            case Action.ACCOUNTS:
                from onboard import onboard
                if not args.accounts:raise SetupError('accounts_file_required')
                result=onboard(target,args.accounts)
            case Action.RUNTIME:
                if not args.spec:raise SetupError('runtime_spec_required')
                result=apply_runtime(target,args.spec,args.typecast_key_file)
            case Action.DOCTOR:
                from doctor import diagnose
                result=diagnose(target)
            case Action.ENABLE:
                from lifecycle import enable
                result=enable(target,consent=args.allow_paid_generation)
            case Action.PAUSE:
                from lifecycle import pause
                result=pause(target)
            case Action.STATUS:
                from doctor import native_workflows
                instance=Instance.load(target)
                state=instance.state
                rows=native_workflows(instance)
                result={'state':state['state'],'automation_enabled':state['automation_enabled'],
                    'active_owned_workflows':sum(bool(w.get('activeVersionId')) for w in rows),
                    'daily_story_limit':state['settings']['daily_story_limit'],
                    'production_interval_minutes':state['settings']['production_interval_minutes'],
                    'production_certified':state.get('production_certified',False)}
            case Action.TEMPLATES:
                from doctor import headers
                instance=Instance.load(target);directory=target/'sheet-templates';directory.mkdir(exist_ok=True,mode=0o700)
                for name,values in zip(('커뮤니티 수집 이력','일일 요약'),headers(instance.root/'source')):
                    with (directory/(name+'.csv')).open('w',encoding='utf-8-sig',newline='') as f:
                        csv.writer(f).writerow(values)
                result={'state':'TEMPLATE_HEADERS_WRITTEN','directory':str(directory),'external_sheet_modified':False}
            case Action.BACKUP:
                from backup import backup
                if not args.output or not args.key_file:raise SetupError('backup_output_and_private_key_file_required')
                result=backup(target,args.output,key_file=args.key_file)
            case unexpected:
                assert_never(unexpected)
        print(json.dumps(result,ensure_ascii=False))
        if action==Action.DOCTOR and result.get('ready') is not True:
            raise SystemExit(2)


if __name__=='__main__':
    try:main()
    except (KeyboardInterrupt,EOFError):
        print(json.dumps({'state':'COMMAND_INTERRUPTED_CHECK_STATUS','reason':'input_cancelled','no_secret_values_printed':True}),file=sys.stderr)
        raise SystemExit(130)
    except (OSError,ValueError,RuntimeError,subprocess.SubprocessError) as exc:
        code=str(exc)
        safe=code if code and all(c.islower() or c.isdigit() or c=='_' for c in code) else 'setup_io_or_subprocess_failed'
        print(json.dumps({'state':'COMMAND_STOPPED','reason':safe,'no_secret_values_printed':True}),file=sys.stderr)
        raise SystemExit(1)
