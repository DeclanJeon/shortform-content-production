"""Verify embedded content instructions and explicitly configured host dependencies."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re

from .contract import canonical_sha256

SKILLS = (
    'creative-production', 'topic-demand-selection', 'designing-character-personas',
    'designing-video-character-sheets', 'storytelling-with-still-images',
    'recording-production-history', 'video-production-assets',
    'orchestrating-video-preproduction', 'developing-video-synopses', 'storyboarding-video',
)
POLICIES = (
    'implementation/content/CONTENT_CREATION_SKILL.md',
    'implementation/content/PUBLISHING_PREFERENCES.md',
    'implementation/content/NARRATIVE_DELIVERY_POLICY.md',
)
STAGES = ('topic', 'narrative', 'storyboard')
MAX_FILE_BYTES = 100_000
MAX_BUNDLE_BYTES = 600_000
MAX_PROMPT_BYTES = 300_000
STORYBOARD_RUNTIME_SCRIPTS = {
    'storyboard_validation': [
        'contents-production-skills/video-production-assets/scripts/validate_project.py',
        'contents-production-skills/video-production-assets/scripts/asset_gate.py',
        'contents-production-skills/video-production-assets/scripts/validate_storyboard.py',
    ],
    'storyboard_renderer': [
        'contents-production-skills/video-production-assets/scripts/render_storyboard_sheet.py',
    ],
    'storyboard_splitter': [
        'contents-production-skills/video-production-assets/scripts/split_storyboard.py',
    ],
}


def _read(root: Path, relative: str, maximum: int = MAX_FILE_BYTES) -> bytes:
    """Reject traversal, links and oversized files before reading trusted input."""
    path = root / relative
    if (Path(relative).is_absolute() or '..' in Path(relative).parts
            or any(p.is_symlink() for p in (path, *path.parents))):
        raise ValueError(f'unsafe_skill_path:{relative}')
    if not path.resolve(strict=True).is_relative_to(root.resolve(strict=True)) or not path.is_file():
        raise ValueError(f'unsafe_skill_path:{relative}')
    if path.stat().st_size > maximum:
        raise ValueError(f'skill_file_too_large:{relative}')
    return path.read_bytes()


def _named(raw: bytes, name: str) -> None:
    if not re.search(r'^name:\s*' + re.escape(name) + r'\s*$', raw.decode('utf-8'), re.M):
        raise ValueError(f'skill_name_mismatch:{name}')


def host_dependencies(host_skill_root: Path, image_executor_script: Path) -> list[dict]:
    """Read the actual host module and exact executor, never an embedded substitute."""
    host = host_skill_root.expanduser().absolute()
    executor = image_executor_script.expanduser().absolute()
    if (not executor.is_relative_to(host) or executor.name != 'codex-imagen.mjs'
            or executor.parent.name != 'scripts' or executor.parent.parent.name != 'codex-imagen'):
        raise ValueError('configured_codex_imagen_executor_required')
    module = _read(host, 'ai-delegation-loop/SKILL.md')
    _named(module, 'ai-delegation-loop')
    relative = executor.relative_to(host).as_posix()
    script = _read(host, relative, 1_000_000)
    return [
        {'path':'host/ai-delegation-loop/SKILL.md',
         'sha256':hashlib.sha256(module).hexdigest(), 'size_bytes':len(module)},
        {'path':'host/' + relative,
         'sha256':hashlib.sha256(script).hexdigest(), 'size_bytes':len(script)},
    ]


def stage_bundle(bundle: dict, stage: str) -> dict:
    """Return only the current stage's instruction files and machine-readable scope."""
    if stage not in STAGES:
        raise ValueError('unknown_skill_stage')
    return bundle['stages'][stage]


def stage_for_operation(operation: str) -> str:
    if operation == 'pitch':
        return 'topic'
    if operation in ('narrative_draft', 'narrative_review', 'narrative_patch'):
        return 'narrative'
    if operation in ('draft', 'review', 'patch', 'adjudicate'):
        return 'storyboard'
    raise ValueError('unknown_skill_operation')


def load_bundle(skill_root, repository_root, *, host_skill_root=None, image_executor_script=None):
    """Pin manifest, all embedded files, unchanged policies and host execution inputs."""
    root = Path(skill_root).expanduser().absolute()
    repo = Path(repository_root).expanduser().absolute()
    manifest_raw = _read(root, 'BUNDLE_MANIFEST.json')
    manifest = json.loads(manifest_raw)
    if (manifest.get('format') != 'shortform-content-skills-v3'
            or manifest.get('upstream_package_version') != '2.30'
            or manifest.get('upstream_commit') != 'dc90ccc2505a60204f9a889c2d2957e444bf38d8'
            or manifest.get('included_skills') != list(SKILLS)
            or manifest.get('external_required_skills') != ['ai-delegation-loop', 'codex-imagen']
            or manifest.get('runtime_scripts') != STORYBOARD_RUNTIME_SCRIPTS
            or set(manifest.get('stage_skill_files', {})) != set(STAGES)):
        raise ValueError('embedded_skill_manifest_invalid')
    content = {}
    files = [{'path':'BUNDLE_MANIFEST.json', 'sha256':hashlib.sha256(manifest_raw).hexdigest()}]
    total = 0
    for entry in manifest['files']:
        relative = entry['path']
        if relative in content or not relative.startswith('contents-production-skills/'):
            raise ValueError('embedded_skill_inventory_invalid')
        raw = _read(root, relative)
        total += len(raw)
        if (len(raw) != entry['size_bytes'] or hashlib.sha256(raw).hexdigest() != entry['sha256']):
            raise ValueError(f'embedded_skill_drift:{relative}')
        content[relative] = raw.decode('utf-8')
        files.append({'path':relative, 'sha256':entry['sha256']})
    actual = set()
    for path in root.rglob('*'):
        if path.is_symlink():
            raise ValueError('embedded_skill_link_forbidden')
        if path.is_file():
            actual.add(path.relative_to(root).as_posix())
    if actual != {'BUNDLE_MANIFEST.json', *content}:
        raise ValueError('embedded_skill_inventory_drift')
    if total > MAX_BUNDLE_BYTES:
        raise ValueError('skill_bundle_too_large')
    for name in SKILLS:
        _named(content[f'contents-production-skills/{name}/SKILL.md'].encode('utf-8'), name)
    for relative in POLICIES:
        raw = _read(repo, relative)
        content[relative] = raw.decode('utf-8')
        files.append({'path':relative, 'sha256':hashlib.sha256(raw).hexdigest()})
    host_setting = host_skill_root or os.environ.get('CREATIVE_HOST_SKILL_ROOT')
    executor_setting = image_executor_script or os.environ.get('CREATIVE_CODEX_IMAGEN_SCRIPT')
    if not host_setting or not executor_setting:
        raise ValueError('explicit_host_skill_root_and_executor_required')
    host = Path(host_setting).expanduser().absolute()
    if host.resolve(strict=True).is_relative_to(root.resolve(strict=True)):
        raise ValueError('host_skills_must_be_external')
    dependencies = host_dependencies(host, Path(executor_setting))
    files.extend(dependencies)
    module = dependencies[0]['path']
    module_raw = _read(host, 'ai-delegation-loop/SKILL.md')
    if hashlib.sha256(module_raw).hexdigest() != dependencies[0]['sha256']:
        raise ValueError('host_skill_changed_during_load')
    content[module] = module_raw.decode('utf-8')
    by_path = {entry['path']:entry for entry in files}
    stages = {}
    for stage, paths in manifest['stage_skill_files'].items():
        labels = list(paths) + list(POLICIES) + [module]
        if len(labels) != len(set(labels)) or any(label not in content for label in labels):
            raise ValueError('embedded_stage_files_invalid')
        text = '\n'.join('\n## INSTALLED INSTRUCTIONS: ' + label + '\n' + content[label] for label in labels)
        limitation = manifest.get('stage_limitations', {}).get(stage)
        if limitation:
            text += '\n## STAGE APPLICABILITY\n' + limitation
        if len(text.encode('utf-8')) > MAX_PROMPT_BYTES:
            raise ValueError('skill_stage_prompt_too_large')
        stages[stage] = {'stage':stage, 'files':[by_path[label] for label in labels], 'text':text}
    return {'revision':canonical_sha256(files), 'files':files, 'stages':stages,
            'narrative_first':True, 'stage_limitations':manifest['stage_limitations']}
