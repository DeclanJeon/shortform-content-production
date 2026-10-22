"""Attach user-owned runtime/auth/skills to an inactive portable instance."""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import tempfile

from installer import atomic
from instance import Instance, JsonObject, SetupError, write_environment


@dataclass(frozen=True, slots=True)
class RuntimeSettings:
    pi: Path
    skills: Path
    executor: Path
    auth: Path
    provider: str
    writer: str
    reviewer: str
    vision: str
    channel: str

    @classmethod
    def read(cls, path: Path) -> RuntimeSettings:
        raw=json.loads(path.read_text())
        fields={'pi_runtime_path','skill_root','image_executor_script','pi_auth_dir',
                'provider','writer_model','reviewer_model','vision_model','channel_name'}
        if not isinstance(raw,dict) or set(raw)!=fields or any(not isinstance(v,str) or not v.strip() for v in raw.values()):
            raise SetupError('runtime_settings_requires_paths_models_not_secrets')
        for key in ('provider','writer_model','reviewer_model','vision_model'):
            if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_./:@+-]{0,199}',raw[key]):
                raise SetupError('exact_provider_and_model_ids_required')
        if len(raw['channel_name'])>40 or any(ord(c)<32 for c in raw['channel_name']):
            raise SetupError('invalid_channel_name')
        paths=[]
        for key in ('pi_runtime_path','skill_root','image_executor_script','pi_auth_dir'):
            p=Path(raw[key]).expanduser()
            if not p.is_absolute():raise SetupError('absolute_runtime_paths_required')
            if key in ('skill_root','image_executor_script') and any(part.is_symlink() for part in (p,*p.parents)):
                raise SetupError('host_skill_links_forbidden')
            paths.append(p.resolve(strict=True))
        pi,skills,executor,auth=paths
        if not pi.is_dir() or not (pi/'dist/cli.js').is_file():raise SetupError('installed_pi_package_required')
        if json.loads((pi/'package.json').read_text()).get('name')!='@earendil-works/pi-coding-agent':
            raise SetupError('installed_pi_package_required')
        if not skills.is_dir() or not auth.is_dir() or not executor.is_file() or not executor.is_relative_to(skills):
            raise SetupError('executor_must_be_inside_host_skill_root')
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
        from creative.skills import host_dependencies
        host_dependencies(skills,executor)
        return cls(pi,skills,executor,auth,raw['provider'],raw['writer_model'],raw['reviewer_model'],raw['vision_model'],raw['channel_name'])


def socket_group() -> int:
    path=Path('/var/run/docker.sock')
    if not stat.S_ISSOCK(path.stat().st_mode):raise SetupError('local_docker_socket_required')
    return path.stat().st_gid


def bundle_revision(source: Path, skills: Path, executor: Path) -> str:
    """Keep canonical instruction labels identical for checkout and package layouts."""
    sys.path.insert(0,str(source))
    from creative.skills import load_bundle
    with tempfile.TemporaryDirectory(prefix='shortform-bundle-') as directory:
        repo=Path(directory)
        shutil.copytree(source/'content',repo/'implementation/content')
        return load_bundle(source/'skills',repo,host_skill_root=skills,
                           image_executor_script=executor)['revision']


def configure_runtime(target: Path, spec: Path) -> JsonObject:
    instance=Instance.load(target)
    if instance.state.get('automation_enabled') is not False:
        raise SetupError('pause_before_runtime_configuration')
    parsed=RuntimeSettings.read(spec)
    if os.getuid()==0:raise SetupError('run_runtime_setup_as_non_root_docker_user')
    source=instance.root/'source'
    revision=bundle_revision(source,parsed.skills,parsed.executor)
    executor_path='/host-skills/'+parsed.executor.relative_to(parsed.skills).as_posix()
    config={'runtime_enabled':False,'ssh_alias':'local',
        'gateway_container':instance.state['settings']['project']+'-gateway',
        'worker_container':instance.state['settings']['project']+'-worker',
        'provider':parsed.provider,'model':parsed.writer,'planning_reviewer_model':parsed.reviewer,
        'vision_model':parsed.vision,'channel_name':parsed.channel,'model_timeout_seconds':300,
        'repository_root':'/repository','skill_root':'/skills','production_root':'/production',
        'host_skill_root':'/host-skills',
        'state_dir':'/state','codex_imagen_script':executor_path,
        'caption_style_path':'/repository/implementation/worker/adapters/caption_style_v10.json',
        'auto_media_enabled':True,'auto_policy_enabled':True,'auto_retention_enabled':True,
        'auto_buffer_enabled':True,'observe_cycles':True,'poll_seconds':30}
    for folder in ('production','coordinator-state'):
        (instance.root/folder).mkdir(mode=0o700,exist_ok=True)
    def mount(host: Path, inside: str, readonly: bool = False) -> JsonObject:
        return {'type':'bind','source':str(host),'target':inside,'read_only':readonly}
    service={'build':{'context':str(source),'dockerfile':'distribution/creative.Dockerfile',
            'args':{'CREATIVE_UID':str(os.getuid()),'CREATIVE_GID':str(os.getgid())}},
        'profiles':['creative'],'init':True,'restart':'unless-stopped','read_only':True,
        'cap_drop':['ALL'],'security_opt':['no-new-privileges:true'],'group_add':[str(socket_group())],
        'tmpfs':['/tmp:mode=1777,size=768m'],'mem_limit':'1536m','pids_limit':128,
        'environment':{'CREATIVE_HOST_SKILL_ROOT':'/host-skills',
                       'CREATIVE_CODEX_IMAGEN_SCRIPT':executor_path},
        'volumes':[mount(Path('/var/run/docker.sock'),'/var/run/docker.sock'),
            mount(instance.root/'creative-runtime.json','/config/creative-runtime.json',True),
            mount(parsed.pi,'/opt/pi-runtime',True),mount(source/'skills','/skills',True),
            mount(parsed.skills,'/host-skills',True),
            mount(parsed.auth,'/auth'),mount(instance.root/'production','/production'),
            mount(instance.root/'coordinator-state','/state'),mount(source,'/repository/implementation',True)]}
    overlay={'services':{'coordinator':service,
        'gateway':{'container_name':config['gateway_container']},
        'worker':{'container_name':config['worker_container']}}}
    atomic(instance.root/'creative-runtime.json',json.dumps(config,ensure_ascii=False,indent=2)+'\n')
    atomic(instance.root/'runtime-compose.json',json.dumps(overlay,indent=2)+'\n')
    write_environment(instance,{'CREATIVE_SKILL_REVISION':revision})
    instance.save_state(state='RUNTIME_CONFIGURED_INACTIVE',runtime_configured=True)
    return {'state':'RUNTIME_CONFIGURED_INACTIVE','skill_revision':revision,
        'automation_enabled':False,'provider_auth_verified':False,
        'warning':'Docker socket access is trusted-administrator access, not a security sandbox.'}
