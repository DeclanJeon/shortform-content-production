#!/usr/bin/env python3
"""Assemble canonical storyboard panels into ordered annotated PNG sheets.

Reads the optional storyboard extension: each panel's registered clean image
or source-sheet crop (EXIF orientation applied, content preserved), verified
against current file hashes, plus shot/scene/beat/character metadata, and
writes output PNG(s) whose captions live OUTSIDE the clean pixels. v5.1 caps
every sheet at 8 panels; boards with more panels render an ordered sheet set
(`<stem>_sNN.png`). Each sheet records deterministic panel/scene crop bounds
in PNG text metadata so split_storyboard can extract clean panel images and
per-scene overview bands without guessing. The caller registers the returned
provenance on storyboard_sheet artifacts/assets; this script never mutates
project.json, never creates a rival manifest, and certifies structure and
file integrity only — not image readability, identity or creative approval.
Pillow is required; no network access or media generation happens here.
"""
import argparse
import copy
import hashlib
import json
import math
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

from validate_project import validate
from validate_storyboard import validate_storyboard
from asset_gate import check_asset_gate

try:
    from PIL import Image, ImageDraw, ImageFont, ImageOps
except ImportError:  # pragma: no cover - environment check
    raise RuntimeError(
        'render_storyboard_sheet requires Pillow (pip install Pillow)'
    )

# The renderer exists to CREATE the sheet a strict full-package gate requires.
# Plan checks therefore run on a scratch copy with reviewed/approved package
# gates demoted for construction; the real ledger is never read as complete
# and never mutated.
_GATE_TYPES = {'preproduction_review': ('reviewed', 'approved'),
               'video_execution_plan': ('approved',)}


def _construction_scratch(project):
    """Deep copy with package-level approval states demoted so construction
    validation cannot require the very deliverable being rendered."""
    scratch = copy.deepcopy(project)
    rows = scratch.get('artifacts')
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            gated = _GATE_TYPES.get(row.get('type'), ())
            if row.get('status') in gated:
                row['status'] = 'draft'
                row.pop('approval', None)

    return scratch

VALID_SOURCE_STATUS = ('available', 'verified')

# Layout constants. Pixel cell size is bounded so the sheet stays inspectable;
# caption text wraps and expands the cell, never clips a required row.
CELL_IMAGE_WIDTH = 560
CELL_IMAGE_HEIGHT = 560
CAPTION_WIDTH = CELL_IMAGE_WIDTH
GUTTER = 16
MARGIN = 16
FONT_SIZE = 17
LINE_SPACING = 6
CAPTION_MAX_LINES = 40
MAX_COLUMNS = 4                # v5.1 grid caps at 4 columns (7–8 panels = 2x4)
MAX_PANELS_PER_SHEET = 8       # v5.1 absolute maximum panels per sheet
MAX_PANELS = 400
MAX_SHEET_PIXELS = 64_000_000  # Bound decoded sheet memory before allocation.

# EXIF orientations that swap width/height (transposed preview dimensions).
_TRANSPOSED_EXIF = frozenset((5, 6, 7, 8))
_EXIF_ORIENTATION_TAG = 0x0112


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


def _table(project, key):
    rows = project.get(key)
    if not isinstance(rows, list):
        return {}
    return {row['id']: row for row in rows
            if isinstance(row, dict) and isinstance(row.get('id'), str)
            and row['id'].strip()}


def _selected_board(project):
    rows = _table(project, 'artifacts')
    package = project.get('preproduction')
    board_id = package.get('storyboard_artifact_id') if isinstance(package, dict) else None
    if board_id in rows:
        return rows[board_id]
    boards = [row for row in rows.values() if row.get('type') == 'storyboard']
    return boards[0] if len(boards) == 1 else None


def _text(value):
    return value.strip() if isinstance(value, str) and value.strip() else None


def _decode_size(path, label, errors):
    """Return EXIF-corrected (width, height) without holding the image open."""
    try:
        with Image.open(path) as im:
            im.load()
            orientation = 1
            try:
                orientation = im.getexif().get(_EXIF_ORIENTATION_TAG, 1)
            except Exception:  # noqa: BLE001 - unreadable EXIF is not fatal
                orientation = 1
            w, h = im.size
    except Exception as e:  # noqa: BLE001 - any decode failure must be an error row
        errors.append(f'{label}: unreadable image ({e})')
        return None
    if orientation in _TRANSPOSED_EXIF:
        return (h, w)
    return (w, h)


def _check_source(panel_id, panel, assets, base, errors):
    """Return (is_crop, asset_id, path, crop_box, file_sha256) for the actual
    pixel source of one panel, or None when errors were recorded."""
    sheet_id = panel.get('source_sheet_asset_id')
    image_id = panel.get('image_asset_id')
    box = panel.get('crop_box')
    if (sheet_id is not None) != (box is not None):
        errors.append(f'{panel_id}: source_sheet_asset_id and crop_box must appear together')
    if sheet_id is not None:
        asset = assets.get(sheet_id) if isinstance(sheet_id, str) else None
        if asset is None:
            errors.append(f'{panel_id}: unknown source_sheet_asset_id {sheet_id}')
            return None
        label = f'{panel_id}: source sheet {sheet_id}'
    else:
        if image_id is None:
            errors.append(f'{panel_id}: image_asset_id or source_sheet_asset_id+crop_box required')
            return None
        asset = assets.get(image_id) if isinstance(image_id, str) else None
        if asset is None:
            errors.append(f'{panel_id}: unknown image_asset_id {image_id}')
            return None
        label = f'{panel_id}: image {image_id}'
    if asset.get('status') not in VALID_SOURCE_STATUS:
        errors.append(f'{label}: asset must be available or verified')
    path = _confined(base, asset.get('path'), label, errors)
    file_sha = None
    if path is not None and not path.is_file():
        errors.append(f'{label}: file missing under the project base')
        path = None
    if path is not None:
        file_sha = _sha256(path)
        digest = asset.get('sha256')
        if digest is not None:
            if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest):
                errors.append(f'{label}: invalid recorded sha256')
            elif file_sha != digest:
                errors.append(f'{label}: file hash does not match the ledger')
    crop = None
    if sheet_id is not None:
        ok = (isinstance(box, list) and len(box) == 4
              and all(isinstance(v, int) and not isinstance(v, bool) for v in box)
              and box[0] >= 0 and box[1] >= 0 and box[2] > box[0] and box[3] > box[1])
        if not ok:
            errors.append(f'{panel_id}: crop_box must be nonnegative ints '
                          '[left,top,right,bottom] with right>left, bottom>top')
        else:
            crop = tuple(box)
    return (sheet_id is not None, asset.get('id'), path, crop, file_sha)



# ---------------------------------------------------------------------------
# Fonts: explicit path or platform discovery; never silently fall back to a
# font that cannot draw the caption text (Korean included).
# ---------------------------------------------------------------------------

_PROBE_TEXT = '패널Aa0'  # one Hangul syllable block + Latin + digit


def _font_ok(path, size):
    try:
        font = ImageFont.truetype(str(path), size)
    except Exception:  # noqa: BLE001 - unusable candidate
        return None
    try:
        if font.getbbox(_PROBE_TEXT) is None:
            return None
        for ch in _PROBE_TEXT:
            if font.getmask(ch).getbbox() is None:
                return None
    except Exception:  # noqa: BLE001
        return None
    return font


def _font_candidates():
    windir = os.environ.get('WINDIR')
    if windir:
        fonts = Path(windir) / 'Fonts'
        for name in ('malgun.ttf', 'malgunsl.ttf', 'NanumGothic.ttf',
                     'gulim.ttc', 'batang.ttc'):
            yield fonts / name
    if sys.platform == 'darwin':
        for name in ('AppleSDGothicNeo.ttc', 'AppleGothic.ttf', 'NanumGothic.ttf'):
            yield Path('/System/Library/Fonts') / name
            yield Path('/Library/Fonts') / name
    for root in ('/usr/share/fonts', '/usr/local/share/fonts',
                 str(Path.home() / '.fonts')):
        base = Path(root)
        if base.is_dir():
            for pattern in ('**/Nanum*.ttf', '**/Nanum*.otf',
                            '**/NotoSansCJK*.ttc', '**/NotoSansCJK*.otf',
                            '**/NotoSansKR*.otf', '**/NotoSansKR*.ttf'):
                yield from sorted(base.glob(pattern))
    match = shutil.which('fc-match')
    if match:
        import subprocess
        try:
            out = subprocess.run([match, '-f', '%{file}', ':lang=ko'],
                                 capture_output=True, text=True, timeout=10)
            if out.returncode == 0 and out.stdout.strip():
                yield Path(out.stdout.strip())
        except (OSError, subprocess.SubprocessError):
            pass


def resolve_font(font_path=None, size=FONT_SIZE):
    """Return (truetype font, resolved path). Raises ValueError when no font
    can draw the required Korean/Latin caption text."""
    errors = []
    if font_path is not None:
        candidate = Path(font_path)
        font = _font_ok(candidate, size) if candidate.is_file() else None
        if font is None:
            raise ValueError(
                f'font {candidate}: unusable or missing Korean/Latin glyphs; '
                'supply a Unicode font that covers the caption text')
        return font, str(candidate.resolve())
    for candidate in _font_candidates():
        if not candidate.is_file():
            continue
        font = _font_ok(candidate, size)
        if font is not None:
            return font, str(candidate.resolve())
        errors.append(str(candidate))
    tried = f' (rejected: {"; ".join(errors)})' if errors else ''
    raise ValueError(
        'no usable caption font found: install a Korean Unicode font '
        '(e.g. Malgun Gothic, Nanum Gothic, Noto Sans CJK KR) or pass '
        f'--font PATH.ttf{tried}')


# ---------------------------------------------------------------------------
# Caption assembly and measured wrapping (expand cells, never clip).
# ---------------------------------------------------------------------------

def _fmt_seconds(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool) \
            and math.isfinite(value):
        text = f'{value:.3f}'.rstrip('0').rstrip('.')
        return f'{text}s'
    return '?'


def _character_refs(character_ids, characters, artifacts):
    parts = []
    for cid in character_ids:
        row = characters.get(cid, {})
        extras = []
        ssot = _text(row.get('ssot_artifact_id'))
        ident = _text(row.get('identity_sheet_asset_id'))
        if ssot:
            extras.append(f'SSOT {ssot}@{artifacts.get(ssot, {}).get("version", "?")}')
        if ident:
            extras.append(f'identity {ident}')
        persona = row.get('persona')
        role = _text(persona.get('role')) if isinstance(persona, dict) else None
        if role:
            extras.append(role)
        parts.append(f'{cid}({", ".join(extras)})' if extras else cid)
    return ', '.join(parts) if parts else 'none'


def _cell_caption(index, panel, panel_id, shot, beats, characters, audio_cues,
                  source_label, artifacts):
    shot_id = panel.get('shot_id') or '?'
    camera = shot.get('camera') if isinstance(shot.get('camera'), dict) else {}
    beat_ids = [b for b in (panel.get('beat_ids') or []) if isinstance(b, str)]
    beat_bits = []
    for bid in beat_ids:
        beat = beats.get(bid, {})
        event = _text(beat.get('event')) or '?'
        beat_bits.append(f'{bid} [{_text(beat.get("synopsis_locator")) or "?"}] {event}')
    characters_ids = [c for c in (panel.get('visible_character_ids') or [])
                      if isinstance(c, str)]
    audio_bits = []
    for cue_id in panel.get('audio_cue_ids', []):
        cue = audio_cues.get(cue_id, {})
        audio_bits.append(
            f'{cue_id} ({_text(cue.get("layer")) or "?"} '
            f'{_fmt_seconds(cue.get("start_s"))}–{_fmt_seconds(cue.get("end_s"))})')
    speech_by_id = {
        line.get('id'): line for line in (shot.get('speech') or [])
        if isinstance(line, dict) and isinstance(line.get('id'), str)
    }
    speech_bits = []
    for speech_id in panel.get('speech_ids', []):
        line = speech_by_id.get(speech_id, {})
        speaker = line.get('character_id') or line.get('kind') or '?'
        speech_bits.append(
            f'{speech_id} {speaker}: “{_text(line.get("text")) or "?"}” '
            f'[{_text(line.get("performance")) or "?"}]')
    cam_bits = ' · '.join(
        f'{k} {_text(camera.get(k)) or "?"}'
        for k in ('shot_size', 'angle', 'framing', 'movement'))
    cam_start = _text(camera.get('start')) or '?'
    cam_end = _text(camera.get('end')) or '?'
    vfx = shot.get('vfx') if isinstance(shot.get('vfx'), dict) else {}
    vfx_text = _text(vfx.get('description')) or ('unused' if vfx.get('enabled') is False else '?')
    return [
        f'{index:03d} · {panel_id} · {shot.get("scene_id") or "?"} · {shot_id} · '
        f'role {_text(panel.get("role")) or "?"} · t={_fmt_seconds(panel.get("frame_time_s"))} '
        f'(shot {_fmt_seconds(shot.get("start_s"))}–{_fmt_seconds(shot.get("end_s"))})',
        'beat ' + (' | '.join(beat_bits) if beat_bits else '?'),
        'camera ' + cam_bits + f' · start {cam_start} · end {cam_end}',
        'visible characters ' + _character_refs(characters_ids, characters, artifacts),
        'audio ' + (' | '.join(audio_bits) if audio_bits else 'none'),
        'speech ' + (' | '.join(speech_bits) if speech_bits else 'none'),
        'reveals ' + (_text(panel.get('reveals')) or '?')
        + ' · withholds ' + (_text(panel.get('withholds')) or '?'),
        'continuity ' + (_text(panel.get('continuity')) or '?'),
        f'vfx {"on" if vfx.get("enabled") else "off"}: {vfx_text}',
        'action ' + (_text(panel.get('visual_action')) or '?'),
        f'source {source_label}',
    ]


def _wrap_line(draw, line, font, width):
    """Word-wrap with character-level fallback; returns list of lines."""
    if not line:
        return ['']
    out, current = [], ''
    def fits(text):
        return draw.textlength(text, font=font) <= width
    for word in line.split(' '):
        trial = word if not current else current + ' ' + word
        if fits(trial):
            current = trial
            continue
        if current:
            out.append(current)
        if fits(word):
            current = word
            continue
        # Split an overlong token character by character.
        piece = ''
        for ch in word:
            if fits(piece + ch):
                piece += ch
            else:
                if piece:
                    out.append(piece)
                piece = ch
        current = piece
    if current:
        out.append(current)
    return out


def _wrap_caption(draw, lines, font, width):
    wrapped = []
    for line in lines:
        wrapped.extend(_wrap_line(draw, line, font, width))
    return wrapped


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------

def _preflight(project, base_dir):
    """Validate the board and decode every panel source into render jobs.
    Returns (jobs, base); raises ValueError."""
    if not isinstance(project, dict):
        raise ValueError('project must be an object')
    storyboard = project.get('storyboard')
    if not isinstance(storyboard, dict) or not isinstance(storyboard.get('panels'), list):
        raise ValueError('project.storyboard.panels array required')
    panels = storyboard['panels']
    if not panels:
        raise ValueError('storyboard.panels is empty; nothing to render')
    if len(panels) > MAX_PANELS:
        raise ValueError(f'{len(panels)} panels exceed the {MAX_PANELS} render bound')

    base = Path(base_dir).resolve()
    board = _selected_board(project)
    if board is not None and board.get('finality') == 'final':
        blockers = check_asset_gate(project, artifact_ids=[board['id']])
        if blockers:
            raise ValueError('FINAL storyboard asset gate blocked: ' + '; '.join(blockers))

    # Full structural gate on a construction scratch copy: the ledger can be
    # mid-build (its completeness requirements are exactly what this sheet is
    # about to satisfy), so reviewed/approved package gates are demoted in the
    # copy. Board checks never require the sheet artifact to exist already.
    scratch = _construction_scratch(project)
    board_errors = validate_storyboard(scratch, base, require_images=True,
                                      check_project=False)
    plan_errors = validate(scratch, 'plan', base)
    if plan_errors or board_errors:
        raise ValueError('storyboard structure invalid: '
                         + '; '.join(plan_errors + board_errors))

    shots = _table(project, 'shots')
    assets = _table(project, 'asset_registry')

    errors = []
    jobs = []  # dict per panel, story order
    seen = set()
    for index, panel in enumerate(panels, 1):
        label = f'panels[{index - 1}]'
        if not isinstance(panel, dict):
            errors.append(f'{label}: panel must be an object')
            continue
        panel_id = _text(panel.get('id'))
        if panel_id is None:
            errors.append(f'{label}: nonempty id required')
            continue
        key = panel_id.lower()
        if key in seen:
            errors.append(f'{panel_id}: duplicate panel id')
        seen.add(key)
        shot_id = panel.get('shot_id')
        shot = shots.get(shot_id) if isinstance(shot_id, str) else None
        if shot is None:
            errors.append(f'{panel_id}: unknown shot_id {shot_id}')
        source = _check_source(panel_id, panel, assets, base, errors)
        if source is None:
            continue
        is_crop, file_asset_id, path, crop, file_sha = source
        size = _decode_size(path, f'{panel_id}: source', errors) if path else None
        if size is None:
            continue
        if is_crop:
            w, h = size
            left, top, right, bottom = crop
            if right > w or bottom > h:
                errors.append(
                    f'{panel_id}: crop_box {list(crop)} outside image {w}x{h}')
                continue
            panel_size = (right - left, bottom - top)
        else:
            panel_size = size
        jobs.append({'index': index, 'panel': panel, 'panel_id': panel_id,
                     'shot': shot, 'source_asset_id': file_asset_id,
                     'file_asset_id': file_asset_id,
                     'source_sha256': file_sha,
                     'file_sha256': file_sha,
                     'path': path, 'crop': crop, 'size': panel_size})

    if errors:
        raise ValueError('sheet preflight failed: ' + '; '.join(errors))
    if len(jobs) != len(panels):
        raise ValueError('sheet preflight failed: unresolved panel rows')
    return jobs, base


def _columns(count):
    """v5.1 grid: 1 panel 1x1; 2 -> 2x1; 3 -> 3x1; 4 -> 2x2; 5-6 -> 2x3;
    7-8 -> 2x4. Rows additionally break at scene boundaries."""
    if count <= 1:
        return 1
    if count == 2:
        return 2
    if count == 3:
        return 3
    if count == 4:
        return 2
    if count <= 6:
        return 3
    return 4


def _output_paths(base, raw_output, sheet_count):
    """Resolve every sheet path; all must be free before any write."""
    paths = []
    for i in range(1, sheet_count + 1):
        rel = raw_output if sheet_count == 1 else (
            str(Path(raw_output).with_name(
                f'{Path(raw_output).stem}_s{i:02d}.png')).replace('\\', '/'))
        candidate = base / rel
        # lexists catches dangling symlinks; resolve catches links that point
        # outside even when the final file does not yet exist.
        if os.path.lexists(candidate):
            raise ValueError(f'output already exists; refusing to overwrite: {candidate}')
        out = candidate.resolve()
        if out == base or base not in out.parents:
            raise ValueError(f'output escapes project base: {candidate}')
        paths.append(out)
    return paths


def _render_one(project, base, jobs, ctx, font, sheet_index, sheet_count, out):
    """Lay out, draw and atomically write one sheet PNG; returns provenance."""
    storyboard = project['storyboard']
    beats = ctx['beats']
    characters = ctx['characters']
    audio_cues = ctx['audio_cues']
    artifacts = ctx['artifacts']
    measure = ctx['measure']
    source_cache = ctx['source_cache']

    # Prepare cell bitmaps scaled to fit the fixed image cell.
    for job in jobs:
        src = source_cache[job['path']]
        if job['crop'] is not None:
            src = src.crop(job['crop'])
        w, h = job['size']
        scale = min(CELL_IMAGE_WIDTH / w, CELL_IMAGE_HEIGHT / h, 1.0)
        scaled = (max(1, round(w * scale)), max(1, round(h * scale)))
        if scaled != (w, h):
            src = src.resize(scaled, Image.LANCZOS)
        job['bitmap'] = src if 'A' in src.getbands() else src.convert('RGB')
        job['scaled_size'] = scaled

    columns = _columns(len(jobs))
    # Rows break at scene boundaries so each row band holds exactly one scene.
    rows = []
    for job in jobs:
        scene = job['shot'].get('scene_id') if job['shot'] else None
        if not rows or len(rows[-1]['jobs']) >= columns or rows[-1]['scene'] != scene:
            rows.append({'scene': scene, 'jobs': []})
        rows[-1]['jobs'].append(job)

    # v5.1 purity: header is production navigation, not a dashboard or audit.
    scene_ids = list(dict.fromkeys(job['shot']['scene_id'] for job in jobs))
    shot_ids = list(dict.fromkeys(job['panel']['shot_id'] for job in jobs))
    board = _selected_board(project)
    finality = 'final' if board is not None and board.get('finality') == 'final' else 'preliminary'
    header = [
        f"{_text(project.get('project_id')) or '?'} v{_text(project.get('version')) or '?'} — "
        f'{finality.upper()} storyboard sheet {sheet_index}/{sheet_count}',
        f'scenes {scene_ids[0]}–{scene_ids[-1]} · shots {shot_ids[0]}–{shot_ids[-1]} · '
        f'panels {jobs[0]["panel_id"]}–{jobs[-1]["panel_id"]}',
    ]
    header_lines = _wrap_caption(measure, header, font, CAPTION_WIDTH * columns
                                 + GUTTER * (columns - 1))
    if len(header_lines) > 8:
        raise ValueError('header does not fit the sheet header band')

    for job in jobs:
        source_label = (
            f'{job["source_asset_id"]} via {job["file_asset_id"]} '
            f'crop {list(job["crop"])}'
            if job['crop'] is not None else f'{job["source_asset_id"]} clean')
        lines = _cell_caption(job['index'], job['panel'], job['panel_id'],
                              job['shot'] or {}, beats, characters, audio_cues,
                              source_label, artifacts)
        wrapped = _wrap_caption(measure, lines, font, CAPTION_WIDTH)
        if len(wrapped) > CAPTION_MAX_LINES:
            raise ValueError(
                f'{job["panel_id"]}: caption needs {len(wrapped)} lines, over the '
                f'{CAPTION_MAX_LINES}-line bound; shorten shot/panel metadata')
        job['caption'] = wrapped

    line_height = FONT_SIZE + LINE_SPACING
    cell_width = CELL_IMAGE_WIDTH
    header_height = MARGIN + len(header_lines) * line_height + GUTTER
    row_heights = []
    for row in rows:
        caption_lines = max(len(j['caption']) for j in row['jobs'])
        row_heights.append(CELL_IMAGE_HEIGHT + GUTTER + caption_lines * line_height)
    sheet_width = MARGIN * 2 + columns * cell_width + (columns - 1) * GUTTER
    sheet_height = header_height + sum(row_heights) + MARGIN
    if sheet_width * sheet_height > MAX_SHEET_PIXELS:
        raise ValueError(
            f'sheet {sheet_width}x{sheet_height} exceeds the {MAX_SHEET_PIXELS}-pixel '
            'bound; split the board explicitly rather than shrinking captions')

    sheet = Image.new('RGB', (sheet_width, sheet_height), (255, 255, 255))
    draw = ImageDraw.Draw(sheet)
    ink = (20, 20, 20)
    dim = (70, 70, 70)
    y = MARGIN
    for line in header_lines:
        draw.text((MARGIN, y), line, font=font, fill=ink)
        y += line_height
    y += GUTTER

    panel_bounds = []
    scene_bounds = []      # ordered [{'scene_id','bounds':[l,t,r,b]}]
    for row, row_height in zip(rows, row_heights):
        x = MARGIN
        for job in row['jobs']:
            bw, bh = job['scaled_size']
            box_x = x + (CELL_IMAGE_WIDTH - bw) // 2
            box_y = y + (CELL_IMAGE_HEIGHT - bh) // 2
            draw.rectangle([x, y, x + CELL_IMAGE_WIDTH - 1,
                            y + CELL_IMAGE_HEIGHT - 1], outline=(210, 210, 210))
            sheet.paste(job['bitmap'], (box_x, box_y),
                        job['bitmap'] if 'A' in job['bitmap'].getbands() else None)
            # Recorded deterministic crop: the pasted clean image rect.
            panel_bounds.append({'panel_id': job['panel_id'],
                                 'bounds': [box_x, box_y, box_x + bw, box_y + bh]})
            cy = y + CELL_IMAGE_HEIGHT + GUTTER
            for i, line in enumerate(job['caption']):
                draw.text((x, cy), line, font=font, fill=ink if i < 4 else dim)
                cy += line_height
            x += cell_width + GUTTER
        # Scene band: full sheet content width so bands concatenate cleanly;
        # covers only this scene's rows (header excluded by construction).
        band = [MARGIN, y, sheet_width - MARGIN, y + row_height]
        if scene_bounds and scene_bounds[-1]['scene_id'] == row['scene']:
            scene_bounds[-1]['bounds'][3] = band[3]
        else:
            scene_bounds.append({'scene_id': row['scene'], 'bounds': band})
        y += row_height

    provenance = [{
        'panel_id': job['panel_id'],
        'shot_id': job['panel'].get('shot_id'),
        'source_asset_id': job['source_asset_id'],
        'file_asset_id': job['file_asset_id'],
        'crop_box': list(job['crop']) if job['crop'] is not None else None,
        'source_sha256': job['source_sha256'],
    } for job in jobs]
    panel_traceability = [{
        'panel_id': job['panel_id'],
        'scene_id': job['shot'].get('scene_id'),
        'shot_id': job['panel'].get('shot_id'),
        'source_asset_id': job['source_asset_id'],
        'source_asset_version': ctx['assets'][job['source_asset_id']].get('version'),
        'source_sha256': job['source_sha256'],
        'asset_version_refs': dict(job['panel'].get('asset_version_refs') or {}),
        'beat_ids': list(job['panel']['beat_ids']),
        'visible_character_ids': list(job['panel']['visible_character_ids']),
        'audio_cue_ids': list(job['panel']['audio_cue_ids']),
        'speech_ids': list(job['panel']['speech_ids']),
        'caption': list(job['caption']),
    } for job in jobs]
    source_sha256 = {}
    for job in jobs:
        key = job['source_asset_id']
        digest = job['source_sha256']
        if key in source_sha256 and source_sha256[key] != digest:
            raise ValueError(f'{key}: conflicting source hashes across panels')
        source_sha256[key] = digest
    from PIL import PngImagePlugin
    meta = PngImagePlugin.PngInfo()
    meta.add_text('storyboard_sheet.panel_ids',
                  ','.join(job['panel_id'] for job in jobs))
    meta.add_text('storyboard_sheet.source_asset_ids',
                  ','.join(job['source_asset_id'] or '' for job in jobs))
    meta.add_text('storyboard_sheet.source_sha256',
                  json.dumps(source_sha256, sort_keys=True))
    meta.add_text('storyboard_sheet.panel_traceability',
                  json.dumps(panel_traceability, ensure_ascii=False, separators=(',', ':')),
                  zip=True)
    meta.add_text('storyboard_sheet.sheet_index', str(sheet_index))
    meta.add_text('storyboard_sheet.sheet_count', str(sheet_count))
    meta.add_text('storyboard_sheet.finality', finality)
    meta.add_text('storyboard_sheet.panel_bounds',
                  json.dumps(panel_bounds, separators=(',', ':')))
    meta.add_text('storyboard_sheet.scene_bounds',
                  json.dumps(scene_bounds, separators=(',', ':')))

    # Atomic no-overwrite write: unique temp in the output folder + link + unlink.
    out.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(dir=out.parent, prefix='.sheet-',
                                             suffix='.png')
    temp = Path(temp_name)
    try:
        with os.fdopen(descriptor, 'wb') as f:
            sheet.save(f, format='PNG', pnginfo=meta)
            f.flush()
            os.fsync(f.fileno())
        try:
            os.link(temp, out)  # hard link fails if the target appeared meanwhile
        except FileExistsError:
            raise ValueError(
                f'output already exists; refusing to overwrite: {out}')
        except OSError as exc:
            raise OSError('atomic no-overwrite output requires hard-link support') from exc
        temp.unlink(missing_ok=True)
    except Exception:
        temp.unlink(missing_ok=True)
        raise

    return {
        'path': str(out),
        'relative_path': str(out.relative_to(base)),
        'sha256': _sha256(out),
        'width': sheet_width,
        'height': sheet_height,
        'sheet_index': sheet_index,
        'sheet_count': sheet_count,
        'finality': finality,
        'panel_ids': [job['panel_id'] for job in jobs],
        'scene_ids': [row['scene_id'] for row in scene_bounds],
        'panel_bounds': panel_bounds,
        'scene_bounds': scene_bounds,
        'source_asset_ids': [job['source_asset_id'] for job in jobs],
        'source_sha256': source_sha256,
        'panels': provenance,
        'panel_traceability': panel_traceability,
    }


def _render(project, base_dir, output, *, font_path=None, single=False,
            panels_per_sheet=MAX_PANELS_PER_SHEET):
    """Shared driver: preflight, resolve outputs, render each sheet."""
    if not isinstance(panels_per_sheet, int) or isinstance(panels_per_sheet, bool) \
            or not 1 <= panels_per_sheet <= MAX_PANELS_PER_SHEET:
        raise ValueError('panels_per_sheet must be an integer from 1 to 8')
    jobs, base = _preflight(project, base_dir)

    if not isinstance(output, (str, Path)) or not str(output).strip():
        raise ValueError('output: nonempty project-relative path required')
    raw_output = str(output)
    if Path(raw_output).is_absolute() or re.match(r'^[A-Za-z]:', raw_output):
        raise ValueError('output must be a project-relative path inside --base-dir')
    out = (base / raw_output).resolve()
    if out != base and base not in out.parents:
        raise ValueError('output path escapes the project base')
    if out.suffix.lower() != '.png':
        raise ValueError('output must be a .png file')

    chunks = [jobs[i:i + panels_per_sheet]
              for i in range(0, len(jobs), panels_per_sheet)]
    if single and len(chunks) > 1:
        raise ValueError(
            f'{len(jobs)} panels need {len(chunks)} sheets at the '
            f'{panels_per_sheet}-panel per-sheet cap; call render_sheets '
            'or the CLI, which paginates automatically')
    paths = _output_paths(base, raw_output, len(chunks))

    font, font_resolved = resolve_font(font_path)
    ctx = {
        'assets': _table(project, 'asset_registry'),
        'characters': _table(project, 'characters'),
        'beats': {row['id']: row for row in
                  (project['storyboard'].get('beats')
                   if isinstance(project['storyboard'].get('beats'), list) else [])
                  if isinstance(row, dict) and isinstance(row.get('id'), str)},
        'audio_cues': _table(project, 'audio_cues'),
        'artifacts': _table(project, 'artifacts'),
        'measure': ImageDraw.Draw(Image.new('RGB', (8, 8))),
        'source_cache': {},
    }
    # Decode sources once per unique file (EXIF applied) for all sheets.
    for job in jobs:
        path = job['path']
        if path not in ctx['source_cache']:
            with Image.open(path) as im:
                im = ImageOps.exif_transpose(im)
                im.load()
                ctx['source_cache'][path] = im.convert('RGB') \
                    if 'A' not in im.getbands() else im.convert('RGBA')

    sheet_count = len(chunks)
    results = []
    try:
        for i, (chunk, path) in enumerate(zip(chunks, paths), 1):
            result = _render_one(project, base, chunk, ctx, font,
                                 i, sheet_count, path)
            result['font'] = font_resolved
            results.append(result)
    except Exception:
        for result in results:
            Path(result['path']).unlink(missing_ok=True)
        raise
    return results


def render_sheets(project, base_dir, output, *, font_path=None,
                  panels_per_sheet=MAX_PANELS_PER_SHEET):
    """Render the board to one or more PNG sheets (v5.1: at most 8 panels per
    sheet, rows break at scene boundaries). Returns {'sheets': [...]};
    raises ValueError and writes nothing on any failure."""
    return {'sheets': _render(project, base_dir, output,
                              font_path=font_path, single=False,
                              panels_per_sheet=panels_per_sheet)}


def render_sheet(project, base_dir, output, *, font_path=None):
    """Strict single-sheet render: raises when the board needs pagination."""
    return _render(project, base_dir, output, font_path=font_path,
                   single=True)[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project')
    parser.add_argument('--base-dir', required=True)
    parser.add_argument('--output', required=True,
                        help='project-relative output PNG path '
                             '(suffixed _sNN when the board paginates)')
    parser.add_argument('--font', help='explicit Korean-capable .ttf/.otf/.ttc path')
    parser.add_argument('--panels-per-sheet', type=int, default=MAX_PANELS_PER_SHEET,
                        help='readability-based panel limit, 1 through 8')
    args = parser.parse_args()
    try:
        project = json.loads(Path(args.project).read_text(encoding='utf-8'))
        result = render_sheets(project, args.base_dir, args.output,
                               font_path=args.font,
                               panels_per_sheet=args.panels_per_sheet)
    except (OSError, ValueError, RuntimeError) as e:
        print(json.dumps({'ok': False, 'errors': [str(e)]}, ensure_ascii=False))
        return 2
    print(json.dumps({'ok': True, **result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
