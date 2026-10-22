"""Interactive account-neutral setup. Calls existing locked CLI stages, never bypasses gates."""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Callable

from installer import DEFAULT, atomic, settings, verify_source
from instance import Instance, JsonObject, SetupError
from runtime import RuntimeSettings
from wizard_prompt import Console, Terminal, confirm, external_path, identifier, integer, text

Runner = Callable[[str,list[str]],int]


def inputs(folder: Path, name: str) -> JsonObject:
    path=folder/name
    if not path.exists():return {}
    if path.is_symlink() or path.stat().st_mode&0o077 or path.stat().st_uid!=os.getuid():
        raise SetupError('wizard_inputs_must_be_private_and_owned')
    value=json.loads(path.read_text())
    if not isinstance(value,dict):raise SetupError('invalid_wizard_inputs')
    return value


def save(folder: Path, name: str, value: JsonObject) -> Path:
    path=folder/name
    if path.is_symlink():raise SetupError('wizard_input_link_forbidden')
    atomic(path,json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    return path


def workspace(target: Path) -> Path:
    folder=target.parent/('.'+target.name+'-setup')
    marker={'format':'shortform-wizard-v1','target':str(target)}
    if folder.exists():
        if folder.is_symlink() or folder.stat().st_mode&0o077 or folder.stat().st_uid!=os.getuid():
            raise SetupError('wizard_workspace_must_be_private_and_owned')
        if inputs(folder,'wizard.json')!=marker:raise SetupError('wizard_workspace_collision')
    else:
        folder.mkdir(parents=True,mode=0o700)
        save(folder,'wizard.json',marker)
    return folder


def run_cli(source: Path, target: Path, action: str, args: list[str]) -> int:
    # No shell interpolation, secret value arguments or paid-provider calls here.
    result=subprocess.run([sys.executable,'-B',str(source/'distribution/manage.py'),action,
        '--target',str(target),*args],check=False)
    return result.returncode


def configure_core(console: Console, folder: Path, initial: JsonObject, *, installed: bool) -> Path:
    console.say('\n[1/5] 설치 위치와 제작 주기 — 유료 생성/게시 없이 기반 서비스만 설치합니다.')
    if installed:
        console.say('기존 설치 설정과 계정 ID를 유지합니다. 설치 중 이름/포트/주기를 임의 변경하지 않습니다.')
        return save(folder,'settings.json',initial)
    value={**DEFAULT,**initial,**inputs(folder,'settings.json')}
    value['project']=text(console,'Docker 프로젝트 이름',str(value['project']),valid=lambda s:bool(re.fullmatch(r'[a-z][a-z0-9_-]{2,39}',s)))
    value['n8n_port']=integer(console,'n8n 로컬 포트',int(value['n8n_port']),lambda n:1024<=n<=65535)
    value['daily_story_limit']=integer(console,'하루 제작 목표',int(value['daily_story_limit']),lambda n:1<=n<=12)
    value['production_interval_minutes']=integer(console,'최소 제작 간격(분)',int(value['production_interval_minutes']),lambda n:n>=120 and 1440%n==0)
    value['allow_sheet_inspiration']=confirm(console,'제목·요약만 있는 소재를 외부 영감으로 사용할까요?',default=value['allow_sheet_inspiration'] is True)
    path=save(folder,'settings.json',value)
    settings(path)  # Canonical existing parser, before any installation effects.
    return path


def configure_accounts(console: Console, folder: Path, initial: JsonObject) -> Path:
    # Current nonempty native IDs override old wizard defaults. Explicit new
    # responses still replace them, but Enter must never roll mapping B back to A.
    value={**inputs(folder,'accounts.json'),**{k:v for k,v in initial.items() if k in (
        'source_sheet_id','publish_sheet_id','publish_queue_tab','publish_privacy',
        'publish_category_id','archive_folder_id','google_credential_id','typecast_voice_id') and v}}
    source_sheet_id=identifier(console,'소재 Google Sheet ID 또는 URL',str(value.get('source_sheet_id','')))
    publish_sheet_id=identifier(console,'발행 큐 Google Sheet ID 또는 URL (Enter: 소재 시트와 동일)',
        str(value.get('publish_sheet_id') or source_sheet_id))
    result={
        'source_sheet_id':source_sheet_id,
        'publish_sheet_id':publish_sheet_id,
        'publish_queue_tab':text(console,'발행 큐 탭 이름',str(value.get('publish_queue_tab','발행 대기')),
            valid=lambda s:bool(s.strip()) and len(s)<=100 and not any(ord(c)<32 or c in '!/\\\\' for c in s)),
        'publish_privacy':text(console,'새 제작물의 큐 공개설정 (공개/비공개/비공개목록)',
            str(value.get('publish_privacy','공개')),
            valid=lambda s:s in ('공개','비공개','비공개목록')),
        'publish_category_id':integer(console,'YouTube 카테고리 ID',
            int(value.get('publish_category_id',22)),lambda n:1<=n<=9999),
        'archive_folder_id':identifier(console,'비공개 Drive 보관 폴더 ID 또는 URL',str(value.get('archive_folder_id',''))),
        'google_credential_id':identifier(console,'n8n Google Drive/Sheets OAuth credential ID (토큰 아님)',
            str(value.get('google_credential_id',''))),
        'typecast_voice_id':identifier(console,'Typecast 음성 ID',str(value.get('typecast_voice_id',''))),
    }
    return save(folder,'accounts.json',result)


def configure_models(console: Console, folder: Path) -> Path:
    value=inputs(folder,'runtime.json')
    found=shutil.which('pi')
    guess=str(Path(found).resolve().parent.parent) if found else ''
    for key,label,default in (
        ('pi_runtime_path','설치된 Pi 패키지 루트(package.json, dist/cli.js)',guess),
        ('pi_auth_dir','본인 Pi 인증 디렉터리(내용이 아닌 경로)',''),
        ('skill_root','사용 권한이 있는 host skills 루트(내장 contents-production-skills 제외)',''),
        ('image_executor_script','host skills 안의 Codex Imagen 실행기 .mjs 파일 경로',''),
        ('provider','Pi provider ID',''),('writer_model','이야기 작성 모델 ID',''),
        ('reviewer_model','이야기 검수 모델 ID',''),('vision_model','이미지 입력 지원 검수 모델 ID',''),
        ('channel_name','채널 표시 이름','내 채널')):
        value[key]=text(console,label,str(value.get(key,default)))
    checkout=Path(__file__).resolve().parents[1]
    for key in ('pi_auth_dir','skill_root','image_executor_script'):
        external_path(str(value[key]),checkout)
    path=save(folder,'runtime.json',value)
    RuntimeSettings.read(path)  # Existing complete runtime/path parser.
    return path


def wizard(source: Path, target: Path, config: Path | None = None, *,
           console: Console | None = None, execute: Runner | None = None,
           idle: Callable[[Path],bool] | None = None) -> JsonObject:
    """Resumable prompt flow; only a fresh explicit ENABLE may activate production."""
    ui=console if console is not None else Terminal()
    ui.say('Shortform 대화형 설정 — Enter: 기본값, Ctrl+C: 중단. 키/토큰은 소스에 저장하지 않습니다.')
    target=external_path(text(ui,'설치 디렉터리',str(target)),source)
    initial: JsonObject={**DEFAULT,'project':'shortform-content','n8n_port':15680}
    if config is not None:initial=settings(config)
    installed=(target/'install-state.json').exists()
    if installed:
        instance=Instance.load(target)
        if instance.state['source_manifest_sha256']!=verify_source(source):
            ui.say('기존 설치와 소스 버전이 다릅니다. 운영 소스를 자동 덮어쓰지 않습니다. 별도 업데이트/복원 절차를 사용하세요.')
            return {'state':'SOURCE_MIGRATION_REQUIRED_NO_CHANGES','automation_changed':False}
        if instance.state.get('automation_enabled') is True:
            ui.say('이미 활성화된 인스턴스입니다. 변경하려면 먼저 shortform pause를 사용하세요.')
            return {'state':'ALREADY_ENABLED_NO_CHANGES','automation_changed':False}
        from lifecycle import busy
        is_idle=idle(target) if idle is not None else not busy(instance)
        if not is_idle:
            ui.say('기존 작업이 진행 중입니다. 종료 후 다시 실행하세요. 새 설정을 덮어쓰지 않습니다.')
            return {'state':'DRAINING_NO_CHANGES','automation_changed':False}
        initial=instance.state['settings']
    folder=workspace(target)
    lock_path=folder/'wizard.lock'
    if lock_path.is_symlink():raise SetupError('wizard_input_link_forbidden')
    with lock_path.open('a') as lock:
        os.chmod(lock.name,0o600)
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise SetupError('another_setup_wizard_is_running') from None
        run=execute if execute is not None else lambda action,args:run_cli(source,target,action,args)
        return walkthrough(ui,folder,target,initial,installed,run)


def walkthrough(ui: Console, folder: Path, target: Path, initial: JsonObject,
                installed: bool, run: Runner) -> JsonObject:
    core=configure_core(ui,folder,initial,installed=installed)
    if not confirm(ui,'Docker 이미지 다운로드/빌드와 기반 설치를 진행할까요?'):
        return {'state':'INPUTS_SAVED_NO_INSTALL','resume_target':str(target)}
    def stage(action: str, args: list[str]) -> None:
        if run(action,args)!=0:raise SetupError('wizard_'+action+'_failed_resume_after_correction')
    stage('setup',['--config',str(core)])
    ui.say('\n[2/5] 본인 Google OAuth 연결 — http://localhost:'+str(settings(core)['n8n_port']))
    ui.say('n8n 소유자 계정을 만들고 Google Drive/Sheets OAuth를 연결하세요.\n'
           'Google 동의/로그인은 브라우저에서 본인이 수행합니다. Client secret/토큰을 여기에 붙이지 마세요.')
    if not confirm(ui,'OAuth 연결과 소재 시트·발행 큐·비공개 Drive 폴더 준비를 마쳤나요?'):
        return {'state':'WAITING_FOR_ACCOUNTS','resume_target':str(target)}
    accounts=configure_accounts(ui,folder,initial)
    stage('accounts',['--accounts',str(accounts)])
    stage('sheet-templates',[])
    ui.say('생성된 '+str(target/'sheet-templates')+' CSV 헤더를 소재 Sheet의 커뮤니티 수집 이력/일일 요약 첫 행에 넣으세요.')
    ui.say('발행 큐 탭은 별도 youtube_publish_only 프로젝트의 SFP00로 초기화한 뒤 같은 계정에 연결해야 합니다.')
    ui.say('\n[3/5] 창작 실행기 — Pi 인증, 허가된 skills, 호환 이미지 실행기는 별도 준비가 필요합니다.')
    if not confirm(ui,'본인 실행기·모델·Typecast 설정을 진행할까요?'):
        return {'state':'WAITING_FOR_RUNTIME','resume_target':str(target)}
    spec=configure_models(ui,folder)
    key_path=target/'secrets/typecast_api_key'
    reuse=key_path.exists() and key_path.stat().st_size>0 and confirm(ui,'기존 Typecast 키를 유지할까요?',default=True)
    with tempfile.TemporaryDirectory(prefix='secret-',dir=folder) as directory:
        args=['--spec',str(spec)]
        if not reuse:
            secret=ui.secret('Typecast API 키(입력 내용 숨김)')
            if not secret or len(secret)>4096:raise SetupError('typecast_key_file_invalid')
            key=Path(directory)/'typecast.key';atomic(key,secret+'\n')
            args+=['--typecast-key-file',str(key)]
        stage('runtime',args)
    ui.say('\n[4/5] 실제 사전 점검 — 유료 이미지/TTS와 발행은 실행하지 않습니다.')
    if run('doctor',[])!=0:
        ui.say('점검이 통과하지 않았습니다. 자동화를 켜지 않았으며 원인 수정 후 wizard를 다시 실행하세요.')
        return {'state':'READINESS_FAILED_NOT_ENABLED','resume_target':str(target)}
    ui.say('\n[5/5] 제작 자동화 활성화 — 활성화하면 유료 제작과 발행 큐 기록이 가능합니다.\n'
           'YouTube 업로드는 별도 youtube_publish_only 프로젝트만 담당합니다. Docker socket 접근은 관리자 권한입니다.')
    if ui.ask('유료 제작 권한을 허용하려면 ENABLE 입력(Enter: 비활성 유지)')!='ENABLE':
        return {'state':'READY_NOT_ENABLED','resume_target':str(target)}
    stage('enable',['--allow-paid-generation'])
    stage('status',[])
    return {'state':'AUTOMATION_ENABLED','first_complete_episode_verified':False}
