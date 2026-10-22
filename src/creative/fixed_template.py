"""Reuse the supplied bitmap frame; only truthful labels and scene artwork change."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import TypedDict, NotRequired, Callable
from PIL import Image, ImageDraw, ImageOps, ImageFont

PROFILE = 'fixed-template-listener-v1'


class Template(TypedDict):
    file: str
    sha256: str
    source_size: list[int]
    panel_box: list[int]
    title_box: list[int]
    metadata_box: list[int]


class Track(TypedDict):
    id: str
    file: str
    sha256: str
    title: str
    author: str
    mood: str
    source_url: str
    license_url: str
    commercial_youtube_sync: bool


class Registry(TypedDict):
    profile: str
    template: Template
    tracks: list[Track]
    license_evidence_sha256: str


class PresentationOptions(TypedDict):
    presentation_assets: NotRequired[str]


class PresentationError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def registry(directory: Path) -> Registry:
    root = directory.resolve(strict=True)
    manifest = json.loads((root/'manifest.json').read_text())
    if manifest.get('profile') != PROFILE:
        raise PresentationError('presentation_profile_invalid')
    for item in [manifest['template'],*manifest['tracks']]:
        name = item['file']
        if Path(name).name != name or (root/name).is_symlink() or digest(root/name) != item['sha256']:
            raise PresentationError('presentation_asset_changed')
    if not manifest['tracks'] or any(not t.get('commercial_youtube_sync') for t in manifest['tracks']):
        raise PresentationError('presentation_music_rights_missing')
    return manifest


def pin(base: Path, config: PresentationOptions) -> tuple[Path | None, Registry | None]:
    """Never switch an existing media attempt to new timing/paid request inputs."""
    from .media_runtime import _atomic
    marker = base/'presentation-profile.json'
    directory = config.get('presentation_assets') or os.environ.get('SHORTFORM_PRESENTATION_ASSETS')
    if marker.exists():
        prior = json.loads(marker.read_text())
        if prior['profile'] == 'legacy':
            return None, None
        root = Path(prior['directory'])
        if digest(root/'manifest.json') != prior['manifest_sha256']:
            raise PresentationError('presentation_registry_changed')
        return root, registry(root)
    active = base.exists() and any(p.is_file() for p in base.rglob('*'))
    if active or not directory:
        _atomic(marker,{'profile':'legacy'})
        return None, None
    root = Path(directory).resolve(strict=True)
    result = registry(root)
    _atomic(marker,{'profile':PROFILE,'directory':str(root),'manifest_sha256':digest(root/'manifest.json')})
    return root, result


def compose(source: Path, title: str, presentation: tuple[Path, Registry], font: Callable[[int], ImageFont.FreeTypeFont]) -> Image.Image:
    """Original header/border pixels stay intact; fake example metadata is erased."""
    root, manifest = presentation
    item = manifest['template']
    with Image.open(root/item['file']) as original:
        image = original.convert('RGB')
    if list(image.size) != item['source_size']:
        raise PresentationError('template_dimensions_changed')
    draw = ImageDraw.Draw(image)
    mx1,my1,mx2,my2 = item['metadata_box']
    draw.rectangle((mx1,my1,mx2-1,my2-1),fill='white')
    draw.text((30,192),'창작 이야기',font=font(30),fill='#414141')
    x1,y1,x2,y2 = item['title_box']
    draw.rectangle((x1,y1,x2,y2),fill='white')
    for size in range(60,15,-1):
        face = font(size)
        lines, line = [], ''
        for char in title:
            if draw.textbbox((0,0),line+char,font=face)[2] > x2-x1:
                lines.append(line);line = ''
            line += char
        lines.append(line)
        if len(lines)<=2 and len(lines)*(size+8)<=y2-y1:break
    else:
        raise PresentationError('template_title_does_not_fit')
    top = (y1+y2-len(lines)*(size+8))//2
    for i,line in enumerate(lines):
        draw.text((x1,top+i*(size+8)),line,font=face,fill='black')
    x1,y1,x2,y2 = item['panel_box']
    with Image.open(source) as art:
        panel = ImageOps.fit(art.convert('RGB'),(x2-x1,y2-y1),method=Image.Resampling.LANCZOS)
    image.paste(panel,(x1,y1))
    return image.resize((1080,1920),Image.Resampling.LANCZOS)
