#!/usr/bin/env python3
"""Split storyboard panels into ordered clean cut files and a derived manifest.

Two modes:

* Default (per-source): reads each panel's registered source sheet crop or
  existing clean image, preflights every ID/path/crop/overwrite, and emits
  flat 000N_PANELID.png plus split-manifest.json (legacy consumer layout).
* Sheet mode (--sheet): after actual storyboard sheet generation, reads the
  deterministic panel/scene bounds each produced sheet PNG records in its
  storyboard_sheet.* text metadata and extracts real pixel crops — never a
  guessed equal grid and never copies of the source images. Output is
  organized per scene: scenes/<SCENE>/panels/000N_PANELID.png, a scene-level
  scenes/<SCENE>/<SCENE>.png overview containing only that scene's panel
  bands (concatenated across sheets when a scene paginates), plus
  split-manifest.json with exact paths, panel->shot->scene indices,
  checksums, sheet versions and source provenance. Writes are transactional:
  everything is built in a temp dir and committed in one rename, so a
  preflight or mid-write failure leaves no partial output.

In sheet mode, each panel's source ID/version/hash, canonical shot/scene and
asset pins must match the rendered metadata, and panel and scene bounds must be
non-overlapping. Forged or missing provenance is rejected before extraction.

The manifest is a derived registration report, not a new ledger; the project
is never mutated. Pillow is required; no network access or media generation
happens here.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path

try:
    from PIL import Image
except ImportError:  # pragma: no cover - environment check
    raise RuntimeError(
        'split_storyboard requires Pillow (pip install Pillow)'
    )

ID_PATTERN = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]*')
VALID_SOURCE_STATUS = ('available', 'verified')


def _sha256(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def _confined(base, relative, label, errors):
    """Resolve a project-relative asset path inside base_dir."""
    if not isinstance(relative, str) or not relative.strip():
        errors.append(f'{label}: nonempty relative path required')
        return None
    if Path(relative).is_absolute() or re.match(r'^[A-Za-z]:', relative):
        errors.append(f'{label}: path must stay inside the project base')
        return None
    resolved = (base / relative).resolve()
    if resolved != base and base not in resolved.parents:
        errors.append(f'{label}: path escapes the project base')
        return None
    return resolved


def _asset(assets, asset_id):
    return assets.get(asset_id) if isinstance(asset_id, str) else None


def _check_source(panel_id, panel, assets, base, errors):
    """Return (kind, asset, box) for a panel source, or None on errors."""
    sheet_id = panel.get('source_sheet_asset_id')
    image_id = panel.get('image_asset_id')
    box = panel.get('crop_box')
    has_sheet = sheet_id is not None
    has_box = box is not None
    if has_sheet != has_box:
        errors.append(f'{panel_id}: source_sheet_asset_id and crop_box must appear together')
    if has_sheet or has_box:
        if not has_sheet:
            errors.append(f'{panel_id}: crop_box without source_sheet_asset_id')
            return None
        asset = _asset(assets, sheet_id)
        if asset is None:
            errors.append(f'{panel_id}: unknown source_sheet_asset_id {sheet_id}')
            return None
        if asset.get('status') not in VALID_SOURCE_STATUS:
            errors.append(f'{panel_id}: source sheet {sheet_id} must be available or verified')
        path = _confined(base, asset.get('path'), f'{panel_id}: source sheet {sheet_id}', errors)
        if path is not None and not path.is_file():
            errors.append(f'{panel_id}: source sheet file missing for {sheet_id}')
            path = None
        if box is None or not isinstance(box, list) or len(box) != 4:
            errors.append(f'{panel_id}: crop_box must be [left,top,right,bottom]')
            return ('sheet', asset, path, None)
        bad = any(not isinstance(v, int) or isinstance(v, bool) for v in box)
        if bad:
            errors.append(f'{panel_id}: crop_box values must be integers')
            return ('sheet', asset, path, None)
        left, top, right, bottom = box
        if right <= left or bottom <= top:
            errors.append(f'{panel_id}: crop_box must be nonempty')
            return ('sheet', asset, path, None)
        return ('sheet', asset, path, tuple(box))
    # Clean image source: image_asset_id alone.
    if image_id is None:
        errors.append(f'{panel_id}: image_asset_id or source_sheet_asset_id+crop_box required')
        return None
    asset = _asset(assets, image_id)
    if asset is None:
        errors.append(f'{panel_id}: unknown image_asset_id {image_id}')
        return None
    if asset.get('status') not in VALID_SOURCE_STATUS:
        errors.append(f'{panel_id}: clean image {image_id} must be available or verified')
    path = _confined(base, asset.get('path'), f'{panel_id}: image {image_id}', errors)
    if path is not None and not path.is_file():
        errors.append(f'{panel_id}: clean image file missing for {image_id}')
        path = None
    return ('clean', asset, path, None)


def _overlap(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def split_storyboard(project, base_dir, output_dir, sheets=None):
    """Extract panel cuts; returns the manifest dict. Raises ValueError on
    preflight failure. Pass sheets=[project-relative or absolute sheet paths]
    to extract clean panels and scene overviews from produced storyboard
    sheets instead of per-panel sources."""
    if sheets is not None:
        return _split_sheets(project, base_dir, output_dir, sheets)
    base = Path(base_dir).resolve()
    out = Path(output_dir).resolve()
    if out != base and base not in out.parents:
        raise ValueError('output directory must be inside the project base')
    if out.exists():
        raise ValueError(f'output directory already exists: {out}')
    if not isinstance(project, dict):
        raise ValueError('project must be an object')
    storyboard = project.get('storyboard')
    if not isinstance(storyboard, dict) or not isinstance(storyboard.get('panels'), list):
        raise ValueError('project.storyboard.panels array required')
    panels = storyboard['panels']
    if not panels:
        raise ValueError('storyboard.panels is empty; nothing to split')
    def ids(table):
        rows = project.get(table)
        if not isinstance(rows, list):
            return {}
        return {row.get('id'): row for row in rows
                if isinstance(row, dict) and isinstance(row.get('id'), str)}
    shots = ids('shots')
    assets = ids('asset_registry')
    scenes = set(ids('scenes'))
    characters = set(ids('characters'))
    audio_cues = set(ids('audio_cues'))
    beat_rows = storyboard.get('beats')
    beats = {row.get('id') for row in (beat_rows if isinstance(beat_rows, list) else [])
             if isinstance(row, dict) and isinstance(row.get('id'), str)}

    errors = []
    jobs = []          # (index, panel, kind, asset, source_path, box)
    boxes_by_sheet = {}
    seen_panel_ids = set()
    for index, panel in enumerate(panels, 1):
        label = f'panels[{index - 1}]'
        if not isinstance(panel, dict):
            errors.append(f'{label}: panel must be an object')
            continue
        panel_id = panel.get('id')
        if not isinstance(panel_id, str) or not ID_PATTERN.fullmatch(panel_id):
            errors.append(f'{label}: unsafe panel id {panel_id!r}')
            continue
        key = panel_id.lower()
        if key in seen_panel_ids:
            errors.append(f'{panel_id}: duplicate panel id')
        seen_panel_ids.add(key)
        filename = f'{index:04d}_{panel_id}.png'
        shot_id = panel.get('shot_id')
        shot = shots.get(shot_id) if isinstance(shot_id, str) else None
        if shot is None:
            errors.append(f'{panel_id}: unknown shot_id {shot_id}')
        else:
            scene_ref = shot.get('scene_id')
            if not isinstance(scene_ref, str) or scene_ref not in scenes:
                errors.append(f'{panel_id}: shot {shot_id} unknown scene_id {scene_ref!r}')
            for field, table in (('beat_ids', beats), ('character_ids', characters),
                                 ('audio_cue_ids', audio_cues)):
                values = shot.get(field, [])
                if not isinstance(values, list):
                    errors.append(f'{panel_id}: shot {shot_id} {field} must be an array')
                    continue
                for value in values:
                    if not isinstance(value, str) or value not in table:
                        errors.append(f'{panel_id}: shot {shot_id} unknown {field} {value!r}')
        source = _check_source(panel_id, panel, assets, base, errors)
        if source is None:
            continue
        kind, asset, path, box = source
        if kind == 'sheet' and box is not None:
            boxes_by_sheet.setdefault(id(asset), (asset, []))[1].append((panel_id, box))
        jobs.append((index, panel, panel_id, filename, kind, asset, path, box))

    # Open each source sheet once: decode validity plus crop bounds.
    sheet_sizes = {}
    for asset, pairs in boxes_by_sheet.values():
        path = _confined(base, asset.get('path'), 'source sheet', errors)
        if path is None or not path.is_file():
            continue
        try:
            with Image.open(path) as im:
                im.load()
                sheet_sizes[id(asset)] = im.size
        except Exception as e:
            errors.append(f'source sheet {asset.get("id")}: unreadable image ({e})')
            continue
        w, h = sheet_sizes[id(asset)]
        for panel_id, box in pairs:
            left, top, right, bottom = box
            if left < 0 or top < 0 or right > w or bottom > h:
                errors.append(f'{panel_id}: crop_box {box} outside image {w}x{h}')
        for i in range(len(pairs)):
            for j in range(i + 1, len(pairs)):
                if _overlap(pairs[i][1], pairs[j][1]):
                    errors.append(
                        f'{pairs[i][0]}: crop overlaps panel {pairs[j][0]} on sheet {asset.get("id")}')
    # Clean images must decode too.
    for index, panel, panel_id, filename, kind, asset, path, box in jobs:
        if kind != 'clean' or path is None or not path.is_file():
            continue
        try:
            with Image.open(path) as im:
                im.load()
        except Exception as e:
            errors.append(f'{panel_id}: clean image unreadable ({e})')
    if errors:
        raise ValueError('split preflight failed: ' + '; '.join(errors))

    out.mkdir(parents=True)
    entries = []
    try:
        for index, panel, panel_id, filename, kind, asset, path, box in jobs:
            dest = out / filename
            with Image.open(path) as im:
                if kind == 'sheet':
                    im.crop(box).save(dest, format='PNG')
                else:
                    keep = im.convert('RGBA') if 'A' in im.getbands() else im.convert('RGB')
                    keep.save(dest, format='PNG')
            shot = shots.get(panel.get('shot_id'), {})
            entries.append({
                'file': filename,
                'sha256': _sha256(dest),
                'panel_id': panel_id,
                'shot_id': panel.get('shot_id'),
                'scene_id': shot.get('scene_id'),
                'beat_ids': list(shot.get('beat_ids') or []),
                'character_ids': list(shot.get('character_ids') or []),
                'audio_cue_ids': list(shot.get('audio_cue_ids') or []),
                'source_asset_id': asset.get('id'),
                'crop_box': list(box) if box else None,
            })
    except Exception:
        shutil.rmtree(out, ignore_errors=True)
        raise
    manifest = {
        'kind': 'split-manifest',
        'derived': True,
        'project_id': project.get('project_id'),
        'entries': entries,
    }
    (out / 'split-manifest.json').write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    return manifest



# ---------------------------------------------------------------------------
# Produced-sheet mode: deterministic crops from recorded bounds.
# ---------------------------------------------------------------------------

def _bounds(value, label, errors):
    """Validate a [left,top,right,bottom] int list; returns tuple or None."""
    if not (isinstance(value, list) and len(value) == 4
            and all(isinstance(v, int) and not isinstance(v, bool)
                    for v in value)
            and value[0] >= 0 and value[1] >= 0
            and value[2] > value[0] and value[3] > value[1]):
        errors.append(f'{label}: invalid bounds {value!r}')
        return None
    return tuple(value)


def _load_sheet_meta(path, label, errors):
    """Read recorded storyboard_sheet.* text metadata from a produced sheet
    PNG. Returns dict or None when errors were recorded."""
    info = {}
    try:
        with Image.open(path) as im:
            info = dict(im.info)
            im.load()
            size = im.size
    except Exception as e:
        errors.append(f'{label}: unreadable sheet image ({e})')
        return None
    def decode(key, required=True):
        raw = info.get(f'storyboard_sheet.{key}')
        if raw is None:
            if required:
                errors.append(f'{label}: missing storyboard_sheet.{key} '
                              'metadata; use sheets produced by '
                              'render_storyboard_sheet, never guessed crops')
            return None
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            errors.append(f'{label}: malformed storyboard_sheet.{key}')
            return None
    panel_ids_raw = info.get('storyboard_sheet.panel_ids')
    if panel_ids_raw is None:
        errors.append(f'{label}: missing storyboard_sheet.panel_ids metadata')
        return None
    panel_ids = [p for p in panel_ids_raw.split(',') if p]
    if not 1 <= len(panel_ids) <= 8:
        errors.append(f'{label}: produced sheets must contain 1 through 8 panels')
        return None
    panel_bounds = decode('panel_bounds')
    scene_bounds = decode('scene_bounds')
    traceability = decode('panel_traceability')
    source_sha = decode('source_sha256', required=False)
    if not isinstance(panel_bounds, list) or not isinstance(scene_bounds, list):
        errors.append(f'{label}: panel_bounds and scene_bounds must be arrays')
        return None
    if len(panel_bounds) != len(panel_ids):
        errors.append(f'{label}: panel_bounds count {len(panel_bounds)} '
                      f'!= panel_ids count {len(panel_ids)}')
        return None
    w, h = size
    bounds_by_panel = {}
    for item, panel_id in zip(panel_bounds, panel_ids):
        bl = (item or {}).get('bounds') if isinstance(item, dict) else None
        box = _bounds(bl, f'{label}: {panel_id} bounds', errors)
        if not isinstance(item, dict) or item.get('panel_id') != panel_id:
            errors.append(f'{label}: recorded bounds do not match panel {panel_id}')
            continue
        if box is None:
            continue
        if box[2] > w or box[3] > h:
            errors.append(f'{label}: {panel_id} bounds {box} outside '
                          f'sheet {w}x{h}')
            continue
        bounds_by_panel[panel_id] = box
    bands = []
    for item in scene_bounds if isinstance(scene_bounds, list) else []:
        if not isinstance(item, dict) or not isinstance(item.get('scene_id'), str):
            errors.append(f'{label}: malformed scene_bounds entry')
            continue
        box = _bounds(item.get('bounds'), f'{label}: {item["scene_id"]} band', errors)
        if box is None:
            continue
        if box[2] > w or box[3] > h:
            errors.append(f'{label}: {item["scene_id"]} band outside sheet')
            continue
        bands.append((item['scene_id'], box))
    for i, (scene_id, bounds) in enumerate(bands):
        for other_id, other_bounds in bands[i + 1:]:
            if _overlap(bounds, other_bounds):
                errors.append(f'{label}: scene bounds overlap for {scene_id} and {other_id}')
    index_raw = info.get('storyboard_sheet.sheet_index')
    try:
        sheet_index = int(index_raw) if index_raw is not None else None
    except (TypeError, ValueError):
        sheet_index = None
    return {'path': path, 'size': size, 'panel_ids': panel_ids,
            'bounds_by_panel': bounds_by_panel, 'bands': bands,
            'traceability': traceability if isinstance(traceability, list) else [],
            'source_sha256': source_sha if isinstance(source_sha, dict) else {},
            'sheet_index': sheet_index, 'sha256': _sha256(path)}


def _split_sheets(project, base_dir, output_dir, sheets):
    """Extract clean panel crops and per-scene overview images from produced
    storyboard sheets. Transactional: builds in a temp dir, commits in one
    rename; any failure leaves no partial output."""
    if not isinstance(project, dict):
        raise ValueError('project must be an object')
    storyboard = project.get('storyboard')
    if not isinstance(storyboard, dict) or not isinstance(storyboard.get('panels'), list):
        raise ValueError('project.storyboard.panels array required')
    panels = storyboard['panels']
    if not panels:
        raise ValueError('storyboard.panels is empty; nothing to split')
    if not isinstance(sheets, (list, tuple)) or not sheets:
        raise ValueError('sheets: nonempty list of sheet image paths required')

    base = Path(base_dir).resolve()
    out = Path(output_dir).resolve()
    if out == base or base not in out.parents:
        raise ValueError('output directory must be a new directory inside the project base')
    if out.exists():
        raise ValueError(f'output directory already exists: {out}')

    for panel in panels:
        pid = panel.get('id') if isinstance(panel, dict) else None
        if not isinstance(pid, str) or not ID_PATTERN.fullmatch(pid):
            raise ValueError(f'unsafe panel id {pid!r}')
    panel_keys = [panel['id'].casefold() for panel in panels]
    if len(set(panel_keys)) != len(panel_keys):
        raise ValueError('duplicate panel id')
    # Canonical shot->scene map and panel order.
    shots = {row.get('id'): row for row in (project.get('shots') or [])
             if isinstance(row, dict) and isinstance(row.get('id'), str)}
    assets = {row.get('id'): row for row in (project.get('asset_registry') or [])
              if isinstance(row, dict) and isinstance(row.get('id'), str)}
    canonical = [p.get('id') for p in panels if isinstance(p, dict)]
    panel_rows = {p.get('id'): p for p in panels if isinstance(p, dict)}
    shot_of = {pid: (panel_rows[pid].get('shot_id'))
               for pid in canonical}
    scene_of = {pid: (shots.get(shot_of[pid]) or {}).get('scene_id')
                for pid in canonical}

    # Preflight: resolve every sheet inside the project base and decode its
    # recorded metadata before any write.
    errors = []
    resolved = []
    for i, item in enumerate(sheets, 1):
        label = f'sheets[{i}]'
        path = None
        if isinstance(item, (str, Path)) and str(item).strip():
            raw = Path(item)
            candidate = raw if raw.is_absolute() else (base / raw)
            candidate = candidate.resolve()
            if candidate == base or base in candidate.parents:
                path = candidate
            else:
                errors.append(f'{label}: sheet path escapes the project base')
        else:
            errors.append(f'{label}: nonempty sheet path required')
        if path is not None and not path.is_file():
            errors.append(f'{label}: sheet file missing {item}')
            path = None
        if path is not None:
            resolved.append((label, path))
    metas = []
    for label, path in resolved:
        meta = _load_sheet_meta(path, label, errors)
        if meta is not None:
            metas.append(meta)

    # Coverage: union of recorded panel IDs must equal canonical order.
    seen = {}
    extracted = []
    order = 0
    for meta in metas:
        order += 1
        if meta['sheet_index'] != order:
            errors.append('sheet indices must follow the supplied sheet order')
        for panel_id in meta['panel_ids']:
            if panel_id in seen:
                errors.append(f'{panel_id}: appears on more than one sheet')
                continue
            seen[panel_id] = meta
            if panel_id not in meta['bounds_by_panel']:
                continue
            extracted.append((panel_id, meta))
    extracted_ids = [pid for pid, _ in extracted]
    if extracted_ids != canonical:
        missing = [p for p in canonical if p not in seen]
        extra = [p for p in extracted_ids if p not in panel_rows]
        detail = []
        if missing:
            detail.append('missing panels ' + ','.join(missing))
        if extra:
            detail.append('unknown panels ' + ','.join(extra))
        if not detail:
            detail.append('sheet panel order does not match canonical order')
        errors.append('sheet coverage mismatch: ' + '; '.join(detail))
    for pid in canonical:
        if pid not in panel_rows:
            errors.append(f'{pid}: panel missing in canonical board')
            continue
        shot = shots.get(shot_of.get(pid))
        if shot is None:
            errors.append(f'{pid}: unknown shot_id {shot_of.get(pid)!r}')
            continue
        sc = scene_of.get(pid)
        meta = seen.get(pid)
        if not isinstance(sc, str) or not ID_PATTERN.fullmatch(sc):
            errors.append(f'{pid}: unsafe scene_id {sc!r}')
        elif meta is not None and \
                not any(sid == sc for sid, _ in meta['bands']):
            errors.append(f'{pid}: sheet has no {sc} scene band')
        if meta is not None:
            box = meta['bounds_by_panel'].get(pid)
            if box is not None and not any(
                    sid == sc and band[0] <= box[0] and band[1] <= box[1]
                    and band[2] >= box[2] and band[3] >= box[3]
                    for sid, band in meta['bands']):
                errors.append(f'{pid}: panel bounds are outside its scene band')
            matches = [item for item in meta['traceability']
                       if isinstance(item, dict) and item.get('panel_id') == pid]
            provenance = matches[0] if len(matches) == 1 else {}
            if len(matches) != 1:
                errors.append(f'{pid}: exactly one panel provenance record is required')
            source_id = provenance.get('source_asset_id')
            expected_source = (panel_rows[pid].get('source_sheet_asset_id')
                               or panel_rows[pid].get('image_asset_id'))
            if source_id != expected_source or not isinstance(source_id, str):
                errors.append(f'{pid}: sheet source asset does not match canonical panel')
            source = assets.get(source_id) if isinstance(source_id, str) else None
            source_path = (_confined(base, source.get('path'), f'{pid} source', errors)
                           if source is not None else None)
            if (source is None
                    or source.get('version') != provenance.get('source_asset_version')
                    or source.get('sha256') != meta['source_sha256'].get(source_id)
                    or not isinstance(source_path, Path)
                    or not source_path.is_file()
                    or _sha256(source_path) != provenance.get('source_sha256')):
                errors.append(f'{pid}: source asset version/hash changed since sheet rendering')
            if provenance.get('asset_version_refs') != panel_rows[pid].get('asset_version_refs', {}):
                errors.append(f'{pid}: sheet asset version pins do not match canonical panel')
            if provenance.get('shot_id') != shot_of[pid] or provenance.get('scene_id') != scene_of[pid]:
                errors.append(f'{pid}: sheet shot/scene provenance does not match canonical panel')
        if meta is not None:
            for i, left in enumerate(meta['panel_ids']):
                for right in meta['panel_ids'][i + 1:]:
                    a = meta['bounds_by_panel'].get(left)
                    b = meta['bounds_by_panel'].get(right)
                    if a is not None and b is not None and _overlap(a, b):
                        errors.append(f'{left}: panel bounds overlap {right} on sheet')
    if errors:
        raise ValueError('sheet-split preflight failed: ' + '; '.join(errors))

    # Transactional build: sibling temp dir, committed by one rename.
    out.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(dir=out.parent,
                                    prefix=f'.{out.name}.staging-'))
    try:
        scenes_order = []
        for pid in extracted_ids:
            sc = scene_of.get(pid) or 'unassigned'
            if sc not in scenes_order:
                scenes_order.append(sc)
        files = []       # manifest entries (story order)
        scene_records = {sc: {'bands': [], 'panel_ids': []} for sc in scenes_order}
        index = 0
        opened = {}
        try:
            for meta in metas:
                opened[meta['path']] = Image.open(meta['path'])
            for pid, meta in extracted:
                index += 1
                sc = scene_of.get(pid) or 'unassigned'
                box = meta['bounds_by_panel'][pid]
                crop = opened[meta['path']].crop(box)
                rel = f'scenes/{sc}/panels/{index:04d}_{pid}.png'
                dest = staging / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                crop.save(dest, format='PNG')
                prov = next((t for t in meta['traceability']
                             if isinstance(t, dict) and t.get('panel_id') == pid), {})
                source_id = None
                if isinstance(prov, dict):
                    source_id = prov.get('source_asset_id')
                entries_extra = {
                    'sheet_path': meta['path'].relative_to(base).as_posix(),
                    'sheet_index': meta['sheet_index'],
                    'bounds': list(box),
                }
                files.append({
                    'file': rel,
                    'sha256': _sha256(dest),
                    'index': index,
                    'panel_id': pid,
                    'shot_id': shot_of.get(pid),
                    'scene_id': sc,
                    'beat_ids': list(prov.get('beat_ids') or []),
                    'character_ids': list(prov.get('visible_character_ids') or []),
                    'audio_cue_ids': list(prov.get('audio_cue_ids') or []),
                    'source_asset_id': source_id,
                    'source_asset_version': prov.get('source_asset_version'),
                    'source_sha256': meta['source_sha256'].get(source_id),
                    **entries_extra,
                })
                scene_records[sc]['panel_ids'].append(pid)
            for meta in metas:
                for sid, band in meta['bands']:
                    if sid in scene_records:
                        scene_records[sid]['bands'].append((meta['path'], band))
            # Scene overviews: only that scene's bands, vertically concatenated.
            scenes_manifest = []
            for sc in scenes_order:
                bands = scene_records[sc]['bands']
                crops = [opened[p].crop(b) for p, b in bands]
                width = max(c.width for c in crops)
                height = sum(c.height for c in crops)
                overview = Image.new('RGB', (width, height), (255, 255, 255))
                yy = 0
                for c in crops:
                    overview.paste(c.convert('RGB'), (0, yy))
                    yy += c.height
                rel = f'scenes/{sc}/{sc}.png'
                dest = staging / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                overview.save(dest, format='PNG')
                scenes_manifest.append({
                    'scene_id': sc,
                    'file': rel,
                    'sha256': _sha256(dest),
                    'panel_ids': list(scene_records[sc]['panel_ids']),
                    'band_sources': [p.relative_to(base).as_posix() for p, _ in bands],
                })
        finally:
            for im in opened.values():
                im.close()
        manifest = {
            'kind': 'split-manifest',
            'derived': True,
            'mode': 'sheet',
            'project_id': project.get('project_id'),
            'sheets': [{'path': m['path'].relative_to(base).as_posix(), 'sha256': m['sha256'],
                        'sheet_index': m['sheet_index'],
                        'panel_ids': m['panel_ids']} for m in metas],
            'scenes': scenes_manifest,
            'entries': files,
        }
        (staging / 'split-manifest.json').write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        if out.exists():
            raise ValueError(f'output directory appeared during build: {out}')
        os.replace(staging, out)
        staging = None
    finally:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project')
    parser.add_argument('--base-dir', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--sheet', action='append', default=[], metavar='PATH',
                        help='produced storyboard sheet PNG (repeat in sheet '
                             'order); enables per-scene extraction from '
                             'recorded bounds')
    args = parser.parse_args()
    try:
        project = json.loads(Path(args.project).read_text(encoding='utf-8'))
        manifest = split_storyboard(project, args.base_dir, args.output,
                                    sheets=args.sheet or None)
    except (OSError, ValueError, RuntimeError) as e:
        print(json.dumps({'ok': False, 'errors': [str(e)]}, ensure_ascii=False))
        return 2
    print(json.dumps({'ok': True, 'files': len(manifest['entries']),
                      'scenes': len(manifest.get('scenes', [])),
                      'manifest': str(Path(args.output) / 'split-manifest.json')},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
