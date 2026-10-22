#!/usr/bin/env python3
"""Validate the optional production storyboard extension: coverage, references and timing only.

Runs the existing plan profile unchanged, then checks the storyboard contract:
every synopsis beat and scene is covered by shots, the first appearance of each
beat follows the approved synopsis array order, every shot has the required
camera/spatial/vfx/speech/audio metadata and panels whose roles and frame times
cover the exact shot frame range in story order. It never attests artistic,
generated-image or approval quality; --require-images only checks registered
status and that real files exist inside --base-dir.
Final boards additionally require per-panel ``asset_version_refs`` pinning every
referenced critical asset (shot ``asset_ids`` + active LOOK) to its registered
version. ``--panels-per-sheet`` (default 8, must match the renderer) caps panel
coverage per registered ``kind=storyboard_sheet`` asset and expects each sheet
to carry ``panel_ids``/``sheet_index``/``sheet_count``.
"""
import argparse
import json
import math
from pathlib import Path
import sys

from validate_project import validate

PANEL_ROLES = ('start', 'action_peak', 'end', 'hold')
CAMERA_KEYS = ('shot_size', 'angle', 'framing', 'movement', 'start', 'end')
PANEL_TEXT_KEYS = ('visual_action', 'reveals', 'withholds', 'continuity')


def validate_storyboard(project, base_dir=None, require_images=False, *, check_project=True, panels_per_sheet=8):
    # Full-package validation reuses the board checks without recursively
    # entering the shared plan gate. Ordinary callers still validate both.
    try:
        errors = list(validate(project, 'plan', base_dir)) if check_project else []
    except Exception as e:  # noqa: BLE001 - malformed input must not crash
        errors = [f'project: malformed data crashed plan validation: {e}']
    def error(s): errors.append(s)
    def text(v): return isinstance(v, str) and bool(v.strip())
    def number(v):
        try: return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
        except OverflowError: return False
    p = project if isinstance(project, dict) else {}
    fps = p.get('fps')
    fps_ok = number(fps) and fps > 0
    if not fps_ok:
        error('project: fps must be a positive finite number')
    def on_frame(v):
        if not number(v) or not fps_ok:
            return False
        frame = v * fps
        return number(frame) and abs(frame - round(frame)) < 0.001

    def table(key):
        out = {}
        rows = p.get(key)
        if isinstance(rows, list):
            for row in rows:
                if isinstance(row, dict) and text(row.get('id')):
                    out[row['id']] = row
        return out
    scenes = table('scenes')
    characters = table('characters')
    assets = table('asset_registry')
    artifacts = table('artifacts')
    audio = table('audio_cues')
    shots_raw = p.get('shots')
    shots_raw = shots_raw if isinstance(shots_raw, list) else []
    shot_rows = [s for s in shots_raw if isinstance(s, dict)]
    shot_by_id = {s['id']: s for s in shot_rows if text(s.get('id'))}
    # Timeline order, not array order, defines the story sequence.
    ordered = sorted(enumerate(shot_rows), key=lambda t: (
        t[1].get('start_s') if number(t[1].get('start_s')) else float('inf'), t[0]))
    shot_index = {s['id']: pos for pos, (_, s) in enumerate(ordered) if text(s.get('id'))}

    sb = p.get('storyboard')
    if not isinstance(sb, dict):
        error('storyboard: extension object required')
        return errors
    syn = sb.get('synopsis_artifact_id')
    if not text(syn):
        error('storyboard: synopsis_artifact_id must be a nonempty string')
    elif syn not in artifacts:
        error(f'storyboard: unknown artifacts reference {syn}')
    elif artifacts[syn].get('type') not in ('synopsis', 'script', 'beat_map'):
        error('storyboard: synopsis_artifact_id must reference a synopsis/script/beat_map artifact')
    boards = [a for a in artifacts.values() if a.get('type') == 'storyboard']
    board_artifact = None
    if isinstance(p.get('preproduction'), dict):
        declared = p['preproduction'].get('storyboard_artifact_id')
        if not isinstance(declared, str) or not declared.strip():
            error('preproduction.storyboard_artifact_id: nonempty string required')
        elif declared in artifacts and artifacts[declared].get('type') == 'storyboard':
            board_artifact = artifacts[declared]
    if board_artifact is None and len(boards) == 1:
        board_artifact = boards[0]
    board_final = isinstance(board_artifact, dict) and board_artifact.get('finality') == 'final'
    board_stale = isinstance(board_artifact, dict) and board_artifact.get('status') == 'stale'
    look_id = p.get('look_asset_id')
    look_id = look_id if isinstance(look_id, str) and look_id in assets else None

    beat_order = {}
    beats = sb.get('beats')
    if not isinstance(beats, list) or not beats:
        error('storyboard.beats: nonempty array required')
        beats = []
    for i, b in enumerate(beats):
        label = f'storyboard.beats[{i}]'
        if not isinstance(b, dict):
            error(f'{label}: entry must be an object'); continue
        rid = b.get('id')
        if not text(rid):
            error(f'{label}: nonempty id required'); continue
        if rid in beat_order: error(f'{rid}: duplicate beat id')
        else: beat_order[rid] = i
        for k in ('synopsis_locator', 'event', 'emotion'):
            if not text(b.get(k)): error(f'{rid}: {k} must be a nonempty string')

    panels = sb.get('panels')
    if not isinstance(panels, list):
        error('storyboard.panels: array required'); panels = []
    panel_rows = []
    panel_ids = set()
    for i, pn in enumerate(panels):
        label = f'storyboard.panels[{i}]'
        if not isinstance(pn, dict):
            error(f'{label}: entry must be an object'); continue
        rid = pn.get('id')
        if text(rid):
            label = rid
            if rid in panel_ids: error(f'{rid}: duplicate panel id')
            panel_ids.add(rid)
        else:
            error(f'{label}: nonempty id required')
        panel_rows.append((label, pn))

    base = Path(base_dir).resolve() if base_dir is not None else None
    if require_images and base is None:
        error('require_images needs --base-dir to confine and read real image files')
    def check_image(label, aid, what):
        """Real handoff: registered available/verified asset whose file exists inside base_dir."""
        if base is None:
            return  # missing confinement root already reported above
        asset = assets.get(aid)
        if asset is None:
            return  # reference itself already reported
        if asset.get('status') not in ('available', 'verified'):
            error(f'{label}: {what} {aid} is not an available/verified file'); return
        path = asset.get('path')
        if not text(path):
            return  # missing path already reported by plan validation
        resolved = (base / path).resolve()
        try:
            resolved.relative_to(base)
        except ValueError:
            error(f'{label}: {what} path escapes --base-dir'); return
        if not resolved.is_file():
            error(f'{label}: {what} file missing under --base-dir')

    mode = p.get('audio_mode')
    spatial_checked = set()
    def check_spatial_file(label, artifact_id):
        if base is None or artifact_id in spatial_checked:
            return
        spatial_checked.add(artifact_id)
        artifact = artifacts[artifact_id]
        asset_ids = artifact.get('asset_ids', [])
        asset_ids = asset_ids if isinstance(asset_ids, list) else []
        registered = [assets[v] for v in asset_ids
                      if isinstance(v, str) and v in assets]
        camera_files = [v for v in registered
                        if v.get('status') in ('available', 'verified')
                        and text(v.get('path')) and Path(v['path']).suffix.lower() == '.json']
        if not camera_files and not require_images and artifact.get('status') in ('draft', 'reviewed', 'approved'):
            return  # still a text plan, not evidence of completed numerical work
        if len(camera_files) != 1:
            error(f'{label}: numeric spatial artifact needs exactly one registered camera JSON file')
            return
        path = (base / camera_files[0]['path']).resolve()
        if not path.is_relative_to(base):
            error(f'{label}: camera JSON path escapes --base-dir')
            return
        try:
            spec = json.loads(path.read_text(encoding='utf-8'))
            spatial_scripts = str(Path(__file__).resolve().parents[2] / 'camera-spatial-design' / 'scripts')
            if spatial_scripts not in sys.path:
                sys.path.insert(0, spatial_scripts)
            from spatial_spec import reconcile_project
            errors.extend(f'{artifact_id}: {e}' for e in reconcile_project(p, spec, artifact_id))
        except (OSError, ValueError, ImportError) as e:
            error(f'{label}: cannot reconcile camera JSON: {e}')
    used_beats = set()
    used_scenes = set()
    first_appearance = []
    speech_ids = set()
    for _, s in ordered:
        rid = s['id'] if text(s.get('id')) else 'shot'
        a, b = s.get('start_s'), s.get('end_s')
        if isinstance(s.get('scene_id'), str) and s['scene_id'] in scenes:
            used_scenes.add(s['scene_id'])
        bids = s.get('beat_ids')
        if not isinstance(bids, list) or not bids:
            error(f'{rid}: beat_ids must be a nonempty array'); bids = []
        for v in bids:
            if not isinstance(v, str) or v not in beat_order:
                error(f'{rid}: unknown storyboard.beats reference {v}')
            else:
                used_beats.add(v)
                if v not in {x[0] for x in first_appearance}:
                    first_appearance.append((v, beat_order[v]))
        cam = s.get('camera')
        if not isinstance(cam, dict):
            error(f'{rid}: camera must be an object')
        else:
            for k in CAMERA_KEYS:
                if not text(cam.get(k)): error(f'{rid}: camera.{k} must be a nonempty string')
        sp = s.get('spatial')
        if not isinstance(sp, dict):
            error(f'{rid}: spatial must be an object')
        else:
            sm = sp.get('mode')
            if sm == 'numeric':
                aid = sp.get('artifact_id')
                if not isinstance(aid, str) or aid not in artifacts:
                    error(f'{rid}: spatial numeric mode needs a valid artifacts reference')
                elif artifacts[aid].get('type') not in ('camera_spec', 'spatial_spec'):
                    error(f'{rid}: spatial artifact must have type camera_spec or spatial_spec')
                else:
                    check_spatial_file(rid, aid)
            elif sm == 'not_applicable':
                if not text(sp.get('reason')):
                    error(f'{rid}: spatial not_applicable needs a reason')
            else:
                error(f'{rid}: spatial.mode must be numeric or not_applicable')
        vfx = s.get('vfx')
        if not isinstance(vfx, dict):
            error(f'{rid}: vfx must be an object')
        else:
            if not isinstance(vfx.get('enabled'), bool):
                error(f'{rid}: vfx.enabled must be a boolean')
            if not text(vfx.get('description')):
                error(f'{rid}: vfx.description must be a nonempty string')
        cues = s.get('audio_cue_ids')
        if not isinstance(cues, list):
            error(f'{rid}: audio_cue_ids must be an array'); cues = []
        linked = set()
        for v in cues:
            if not isinstance(v, str) or v not in audio:
                error(f'{rid}: unknown audio_cues reference {v}')
            else:
                linked.add(v)
        # Full linkage both ways: a cue linked to a shot must overlap its range,
        # and every cue overlapping the shot (e.g. sequence BGM) must be linked.
        if all(number(v) for v in (a, b)):
            for cid, cue in audio.items():
                ca, cb = cue.get('start_s'), cue.get('end_s')
                if not all(number(v) for v in (ca, cb)):
                    continue  # bad interval already reported by plan validation
                overlaps = ca < b - 1e-9 and cb > a + 1e-9
                if cid in linked and not overlaps:
                    error(f'{rid}: audio cue {cid} does not overlap the shot range')
                elif overlaps and cid not in linked:
                    error(f'{rid}: overlapping audio cue {cid} not linked in audio_cue_ids')
        shot_chars = s.get('character_ids')
        shot_chars = shot_chars if isinstance(shot_chars, list) else []
        speech = s.get('speech')
        if not isinstance(speech, list):
            error(f'{rid}: speech must be an array'); speech = []
        if speech and mode in ('no_audio', 'no_dialogue'):
            error(f'{rid}: {mode} project cannot contain speech')
        for j, item in enumerate(speech):
            label = f'{rid}.speech[{j}]'
            if not isinstance(item, dict):
                error(f'{label}: entry must be an object'); continue
            sid = item.get('id')
            if text(sid):
                label = sid
                if sid in speech_ids: error(f'{sid}: duplicate speech id')
                speech_ids.add(sid)
            else:
                error(f'{label}: nonempty id required')
            kind = item.get('kind')
            if kind not in ('dialogue', 'narration'):
                error(f'{label}: kind must be dialogue or narration')
            for k in ('text', 'performance'):
                if not text(item.get(k)): error(f'{label}: {k} must be a nonempty string')
            va = item.get('voice_artifact_id')
            if not isinstance(va, str) or va not in artifacts:
                error(f'{label}: voice_artifact_id must reference an artifacts entry')
            elif artifacts[va].get('type') not in ('voice_profile', 'voice', 'voice_recording', 'voiceover'):
                error(f'{label}: voice_artifact_id must reference a voice artifact')
            ls = item.get('lip_sync')
            if ls not in ('required', 'not_applicable'):
                error(f'{label}: lip_sync must be required or not_applicable')
            elif kind == 'narration' and ls != 'not_applicable':
                error(f'{label}: narration lip_sync must be not_applicable')
            cid = item.get('character_id')
            if kind == 'dialogue':
                if not isinstance(cid, str) or cid not in shot_chars:
                    error(f'{label}: dialogue character_id must be one of the shot characters')
            elif cid is not None and (not isinstance(cid, str) or cid not in characters):
                error(f'{label}: unknown characters reference {cid}')
            x, y = item.get('start_s'), item.get('end_s')
            if not all(number(v) for v in (x, y)) or x < 0 or y <= x:
                error(f'{label}: invalid speech interval')
            elif number(a) and number(b) and (x < a - 1e-6 or y > b + 1e-6):
                error(f'{label}: speech outside the shot time range')

    last_key = None
    shot_panels = {}
    panel_links_by_shot = {}
    sheet_crops = {}
    for label, pn in panel_rows:
        sid = pn.get('shot_id')
        if not isinstance(sid, str) or sid not in shot_by_id:
            error(f'{label}: unknown shots reference {sid}'); continue
        shot_panels.setdefault(sid, []).append(pn)
        shot = shot_by_id[sid]
        links = panel_links_by_shot.setdefault(
            sid, {'beat_ids': set(), 'visible_character_ids': set(),
                  'audio_cue_ids': set(), 'speech_ids': set()})

        t = pn.get('frame_time_s')
        def panel_refs(field, available, scoped, *, required=False):
            values = pn.get(field)
            if not isinstance(values, list):
                error(f'{label}: {field} must be an array')
                return set()
            if required and not values:
                error(f'{label}: {field} must be a nonempty array')
            linked = set()
            for value in values:
                if not isinstance(value, str):
                    error(f'{label}: {field} entries must be strings')
                    continue
                if value in linked:
                    error(f'{label}: duplicate {field} reference {value}')
                    continue
                linked.add(value)
                if value not in available:
                    error(f'{label}: unknown {field} reference {value}')
                elif value not in scoped:
                    error(f'{label}: {field} reference {value} is not linked to shot {sid}')
            return linked & available & scoped

        def string_set(values):
            return {value for value in values if isinstance(value, str)}
        shot_beats = shot.get('beat_ids') if isinstance(shot.get('beat_ids'), list) else []
        shot_chars = shot.get('character_ids') if isinstance(shot.get('character_ids'), list) else []
        shot_cues = shot.get('audio_cue_ids') if isinstance(shot.get('audio_cue_ids'), list) else []
        shot_speech = shot.get('speech') if isinstance(shot.get('speech'), list) else []
        speech_for_shot = {item.get('id') for item in shot_speech
                           if isinstance(item, dict) and isinstance(item.get('id'), str)}
        panel_beats = panel_refs(
            'beat_ids', set(beat_order), string_set(shot_beats), required=True)
        panel_characters = panel_refs(
            'visible_character_ids', set(characters), string_set(shot_chars))
        panel_cues = panel_refs(
            'audio_cue_ids', set(audio), string_set(shot_cues))
        panel_speech_ids = panel_refs(
            'speech_ids', speech_ids | speech_for_shot, speech_for_shot)
        links['beat_ids'].update(panel_beats)
        links['visible_character_ids'].update(panel_characters)
        links['audio_cue_ids'].update(panel_cues)
        links['speech_ids'].update(panel_speech_ids)

        for cue_id in panel_cues:
            cue = audio.get(cue_id, {})
            start, end = cue.get('start_s'), cue.get('end_s')
            if number(t) and number(start) and number(end) and not start <= t < end:
                error(f'{label}: audio cue {cue_id} does not overlap the panel time')
        speech_by_id = {item['id']: item for item in shot_speech
                        if isinstance(item, dict) and isinstance(item.get('id'), str)}
        for speech_id in panel_speech_ids:
            line = speech_by_id[speech_id]
            start, end = line.get('start_s'), line.get('end_s')
            if number(t) and number(start) and number(end) and not start <= t < end:
                error(f'{label}: speech {speech_id} does not overlap the panel time')
        key = (shot_index.get(sid, len(shot_index)), t if number(t) else float('inf'))
        if last_key is not None and key < last_key:
            error(f'{label}: panels must follow story and frame order')
        last_key = key
        role = pn.get('role')
        if role not in PANEL_ROLES:
            error(f'{label}: role must be start/action_peak/end/hold')
        for k in PANEL_TEXT_KEYS:
            if not text(pn.get(k)): error(f'{label}: {k} must be a nonempty string')
        if not number(t):
            error(f'{label}: frame_time_s must be a finite number')
        else:
            s0, s1 = shot.get('start_s'), shot.get('end_s')
            if number(fps) and not on_frame(t):
                error(f'{label}: frame_time_s is not frame aligned')
            if fps_ok and all(number(v) for v in (s0, s1)):
                if t < s0 - 1e-6 or t > s1 - 1/fps + 1e-6:
                    error(f'{label}: frame_time_s outside the shot frame range')
        aid = pn.get('image_asset_id')
        if not isinstance(aid, str) or aid not in assets:
            error(f'{label}: image_asset_id must reference an asset_registry entry')
        elif require_images:
            check_image(label, aid, 'image asset')
        sheet = pn.get('source_sheet_asset_id')
        if sheet is not None:
            if not isinstance(sheet, str) or sheet not in assets:
                error(f'{label}: unknown source_sheet_asset_id {sheet}')
            elif require_images:
                check_image(label, sheet, 'source sheet')
        cb = pn.get('crop_box')
        if cb is not None:
            ok = (isinstance(cb, list) and len(cb) == 4
                  and all(isinstance(v, int) and not isinstance(v, bool) for v in cb))
            if not ok or cb[0] < 0 or cb[1] < 0 or cb[2] <= cb[0] or cb[3] <= cb[1]:
                error(f'{label}: crop_box must be nonnegative ints [left,top,right,bottom] with right>left, bottom>top')
            elif isinstance(sheet, str):
                sheet_crops.setdefault(sheet, []).append((label, cb))
        avr = pn.get('asset_version_refs')
        if avr is not None:
            if not isinstance(avr, dict):
                error(f'{label}: asset_version_refs must map asset ids to versions'); avr = {}
            for asset_id, expected in avr.items():
                row = assets.get(asset_id)
                if row is None:
                    error(f'{label}: unknown asset_version_refs entry {asset_id}')
                else:
                    if not isinstance(expected, str) or not expected.strip():
                        error(f'{label}: asset_version_refs {asset_id} needs a version string')
                    elif expected != row.get('version') and not board_stale:
                        error(f'{label}: asset_version_refs {asset_id} version drift')
                    if row.get('status') == 'stale' and not board_stale:
                        error(f'{label}: references stale asset {asset_id}')
        if board_final:
            if not isinstance(avr, dict) or not avr:
                error(f'{label}: final storyboard panels need asset_version_refs')
            else:
                required = set(shot.get('asset_ids') if isinstance(shot.get('asset_ids'), list) else [])
                if look_id:
                    required.add(look_id)
                for asset_id in required:
                    if asset_id not in avr:
                        error(f'{label}: final storyboard panel is missing the required asset reference {asset_id}')
    for sheet, crops in sheet_crops.items():
        for i in range(len(crops)):
            for j in range(i + 1, len(crops)):
                (la, ca), (lb, cb) = crops[i], crops[j]
                if ca[0] < cb[2] and cb[0] < ca[2] and ca[1] < cb[3] and cb[1] < ca[3]:
                    error(f'{la}: crop_box overlaps {lb} on {sheet}')

    for _, s in ordered:
        rid = s['id'] if text(s.get('id')) else 'shot'
        rows = shot_panels.get(rid, [])
        if not rows:
            error(f'{rid}: shot has no storyboard panels'); continue
        roles = [pn.get('role') for pn in rows]
        times = [pn.get('frame_time_s') for pn in rows]
        s0, s1 = s.get('start_s'), s.get('end_s')
        has_hold = 'hold' in roles
        if has_hold:
            # hold is only for a genuinely static shot: unchanged start/end state.
            start_state, end_state = s.get('start_state'), s.get('end_state')
            if not (isinstance(start_state, str) and start_state == end_state):
                error(f'{rid}: hold panel requires identical start_state and end_state')
        if all(r == 'hold' for r in roles):
            if len(rows) != 1:
                error(f'{rid}: a static shot allows exactly one hold panel')
            elif number(times[0]) and number(s0) and abs(times[0] - s0) > 1e-6:
                error(f'{rid}: hold panel must sit on the shot start frame')
        else:
            if has_hold:
                error(f'{rid}: hold panels cannot mix with changing-shot roles')
            if roles[0] != 'start':
                error(f'{rid}: first panel must have role start')
            if number(times[0]) and number(s0) and abs(times[0] - s0) > 1e-6:
                error(f'{rid}: first panel must be at the shot start frame')
            if roles[-1] != 'end':
                error(f'{rid}: last panel must have role end')
            if number(times[-1]) and number(s1) and fps_ok:
                if abs(times[-1] - (s1 - 1/fps)) > 1e-6:
                    error(f'{rid}: last panel must be at the shot last frame')
            for r in roles[1:-1]:
                if r != 'action_peak':
                    error(f'{rid}: intermediate panels must have role action_peak'); break
    for sid, shot in shot_by_id.items():
        linked = panel_links_by_shot.get(
            sid, {'beat_ids': set(), 'audio_cue_ids': set(), 'speech_ids': set()})
        for field in ('beat_ids', 'audio_cue_ids'):
            values = shot.get(field) if isinstance(shot.get(field), list) else []
            for value in values:
                if isinstance(value, str) and value not in linked[field]:
                    error(f'{sid}: {field} reference {value} is not linked from any panel')
        speech = shot.get('speech') if isinstance(shot.get('speech'), list) else []
        for item in speech:
            if isinstance(item, dict) and isinstance(item.get('id'), str) \
                    and item['id'] not in linked['speech_ids']:
                error(f'{sid}: speech reference {item["id"]} is not linked from any panel')

    for bid in beat_order:
        if bid not in used_beats:
            error(f'{bid}: beat not covered by any shot')
    for i in range(1, len(first_appearance)):
        if first_appearance[i][1] < first_appearance[i-1][1]:
            error('shot beat_ids reverse the approved synopsis beat order'); break
    for sid in scenes:
        if sid not in used_scenes:
            error(f'{sid}: scene not covered by any shot')
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project')
    parser.add_argument('--base-dir')
    parser.add_argument('--require-images', action='store_true')
    parser.add_argument('--panels-per-sheet', type=int, default=8,
                        help='max panels per storyboard_sheet asset (match the renderer setting)')
    args = parser.parse_args()
    try:
        p = json.loads(Path(args.project).read_text(encoding='utf-8'))
        errors = validate_storyboard(p, args.base_dir, args.require_images, panels_per_sheet=args.panels_per_sheet)
    except (OSError, ValueError) as e:
        print(json.dumps({'valid': False, 'errors': [str(e)]}, ensure_ascii=False)); return 2
    print(json.dumps({'valid': not errors, 'errors': errors,
                      'scope': 'storyboard coverage, references and timing; no media decoding, artistic or approval verification'},
                     ensure_ascii=False, indent=2))
    return 1 if errors else 0
if __name__ == '__main__': raise SystemExit(main())
