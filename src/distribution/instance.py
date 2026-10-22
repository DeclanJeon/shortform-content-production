"""Shared instance boundary for portable setup and lifecycle commands."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json
from pathlib import Path
from typing import Final, TypeAlias

from installer import atomic, dotenv, verify_source

Json: TypeAlias = str | int | float | bool | None | list['Json'] | dict[str, 'Json']
JsonObject: TypeAlias = dict[str, Json]
FLAGS: Final = ('AUTOMATIC_CREATIVE_ENABLED','AUTOMATIC_MEDIA_ENABLED',
                'AUTOMATIC_POLICY_APPROVAL_ENABLED')
WORKFLOWS: Final = ('SFDailySheetIntake','SFAutomaticPrivateDriveArchive',
    'SFAutomaticProjectArchive','SFAutomaticPolicyApproval',
    'SFAutomaticProductionProjection','SFAutomaticPublishQueueHandoff')


class SetupError(ValueError):
    """Typed boundary error; Python must be able to attach traceback metadata."""
    code: str

    def __init__(self, code: str) -> None:
        self.code=code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class Instance:
    root: Path
    state: JsonObject

    @classmethod
    def load(cls, target: Path) -> Instance:
        target=target.absolute()
        if any(p.is_symlink() for p in (target,*target.parents)):
            raise SetupError('unsafe_install_directory')
        state=json.loads((target/'install-state.json').read_text())
        if state.get('format')!='shortform-install-v1':
            raise SetupError('installer_owned_instance_required')
        if verify_source(target/'source')!=state['source_manifest_sha256']:
            raise SetupError('installed_source_changed')
        return cls(target,state)

    def fingerprint(self) -> str:
        """Bind checks to stable configuration, not mutable OAuth refresh tokens."""
        env=dotenv(self.root/'.env')
        for key in (*FLAGS,'PRODUCTION_START_AT'):
            env.pop(key,None)
        runtime=self.root/'creative-runtime.json'
        config=json.loads(runtime.read_text()) if runtime.exists() else None
        if config:
            config.pop('runtime_enabled',None)
        overlay_path=self.root/'runtime-compose.json'
        overlay=json.loads(overlay_path.read_text()) if overlay_path.exists() else None
        dependencies={}
        if overlay:
            mounts={m['target']:Path(m['source']) for m in overlay['services']['coordinator']['volumes']}
            from runtime import bundle_revision
            executor=mounts['/host-skills']/config['codex_imagen_script'].removeprefix('/host-skills/')
            dependencies['skill_revision']=bundle_revision(self.root/'source',mounts['/host-skills'],executor)
            for name,path in [('executor',executor),('history',mounts['/skills']/'contents-production-skills/recording-production-history/scripts/production_history.py'),('pi_package',mounts['/opt/pi-runtime']/'package.json'),('pi_cli',mounts['/opt/pi-runtime']/'dist/cli.js')]:
                dependencies[name]=hashlib.sha256(path.read_bytes()).hexdigest()
        metadata={}
        for name in ('review_callback_secret','typecast_api_key'):
            info=(self.root/'secrets'/name).stat()
            metadata[name]=[info.st_ino,info.st_size,info.st_mtime_ns,info.st_uid,info.st_mode]
        value={'source':self.state['source_manifest_sha256'],'settings':self.state['settings'],
               'environment':env,'runtime':config,'runtime_mounts':overlay,
               'dependencies':dependencies,'secret_file_versions':metadata}
        return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()

    def sign(self, value: JsonObject) -> str:
        payload=json.dumps(value,sort_keys=True,separators=(',',':')).encode()
        return hmac.new(dotenv(self.root/'.env')['API_TOKEN'].encode(),payload,hashlib.sha256).hexdigest()

    def save_state(self, **changes: Json) -> None:
        atomic(self.root/'install-state.json',json.dumps({**self.state,**changes},indent=2)+'\n')


def write_environment(instance: Instance, values: dict[str,str]) -> None:
    env=dotenv(instance.root/'.env')
    env.update(values)
    atomic(instance.root/'.env',''.join(k+'='+v+'\n' for k,v in env.items()))
