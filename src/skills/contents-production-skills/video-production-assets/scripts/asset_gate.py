"""v5.1 asset gate: may declared-FINAL artifacts be produced right now?

check_asset_gate(project, artifact_ids=None) inspects the canonical ledger in
memory — no file system access, no media decoding, no approval attestation.
It returns blocker strings; an empty list means every checked `finality: final`
artifact's required assets are registered, locked and pinned at their current
version, and its dependency chain and complete master ancestry have no missing,
draft, stale or version-drifting links. Final visual panels must also pin all
required assets to their registered current versions.

An asset counts as locked only at status `verified` — the v5.1
LOCKED / APPROVED / VERIFIED_REFERENCE tier. `available` means a file exists
but has not passed its asset QA gate, so a draft/uninspected master with an
available file still BLOCKS final production. A declared final visual board
(type storyboard or storyboard_sheet) additionally requires an ACTIVE LOOK:
project.look_asset_id must point to a registered style-world-bible/look asset
with verified status, and the board must pin its current version in
required_asset_versions. Preliminary work never needs this gate. Pass
`artifact_ids` to scope the check to selected final artifacts (e.g. one
storyboard) so unrelated stale declarations don't block a request; with None
every final artifact is checked.
"""

import hashlib
import json
from pathlib import Path

VISUAL_TYPES = ('storyboard', 'storyboard_sheet')


def check_asset_gate(project, artifact_ids=None):
    """Return blocker strings; [] means the selected final scope is clear."""
    blockers = []
    if not isinstance(project, dict):
        return ['project must be an object']
    assets = {row['id']: row for row in project.get('asset_registry', [])
              if isinstance(row, dict) and isinstance(row.get('id'), str)}
    artifacts = {row['id']: row for row in project.get('artifacts', [])
                 if isinstance(row, dict) and isinstance(row.get('id'), str)}

    def locked(asset_id, expected, owner_label):
        asset = assets.get(asset_id)
        if asset is None:
            blockers.append(f'{owner_label}: required asset {asset_id} is not registered')
        elif asset.get('status') == 'stale':
            blockers.append(f'{owner_label}: required asset {asset_id} is stale')
        elif asset.get('status') != 'verified':
            blockers.append(f'{owner_label}: required asset {asset_id} is not locked '
                            f'(status {asset.get("status")}; verified required)')
        elif expected is not None and asset.get('version') != expected:
            blockers.append(f'{owner_label}: required asset {asset_id} version drift '
                            f'(pinned {expected}, current {asset.get("version")})')
        if asset is None:
            return
        seen = {asset_id}
        current = asset
        while isinstance(current.get('master_asset_ref'), dict):
            reference = current['master_asset_ref']
            master_id = reference.get('asset_id')
            if not isinstance(master_id, str) or master_id in seen:
                blockers.append(f'{owner_label}: invalid or cyclic master lineage for {asset_id}')
                break
            seen.add(master_id)
            master = assets.get(master_id)
            if master is None:
                blockers.append(f'{owner_label}: master asset {master_id} is not registered')
                break
            if master.get('status') != 'verified':
                blockers.append(f'{owner_label}: master asset {master_id} is not verified '
                                f'(status {master.get("status")})')
            if reference.get('version') != master.get('version'):
                blockers.append(f'{owner_label}: master asset {master_id} version drift '
                                f'(pinned {reference.get("version")}, current {master.get("version")})')
            current = master

    selected = None
    if artifact_ids is not None:
        selected = set(artifact_ids)
        unknown = selected - set(artifacts)
        for aid in sorted(unknown):
            blockers.append(f'{aid}: requested artifact is not registered')

    look_id = project.get('look_asset_id')
    look = assets.get(look_id) if isinstance(look_id, str) else None
    final_boards = []
    for aid, artifact in sorted(artifacts.items()):
        if artifact.get('finality') != 'final':
            continue
        if selected is not None and aid not in selected:
            continue
        if artifact.get('type') in VISUAL_TYPES:
            final_boards.append((aid, artifact))
        if artifact.get('status') in ('draft', 'stale'):
            blockers.append(f'{aid}: {artifact.get("status")} artifact cannot be final')

        required = artifact.get('required_asset_versions')
        if not isinstance(required, dict) or not required:
            blockers.append(f'{aid}: final artifact needs required_asset_versions')
        else:
            for asset_id, expected in sorted(required.items()):
                if not isinstance(expected, str) or not expected.strip():
                    blockers.append(f'{aid}: required_asset_versions {asset_id} '
                                    'needs a nonempty version pin')
                locked(asset_id, expected, aid)

        # The active LOOK is a required input of every final visual board:
        # the board must pin the registered look's current version.
        if artifact.get('type') in VISUAL_TYPES:
            if not isinstance(look_id, str) or not look_id.strip():
                blockers.append(f'{aid}: final visual artifact requires an active look_asset_id')
            elif look is None:
                blockers.append(f'{aid}: look_asset_id {look_id} is not a registered asset')
            else:
                kind = look.get('kind')
                if kind not in ('style_world_bible', 'document') \
                        and look.get('entity_type') != 'look':
                    blockers.append(f'{aid}: look_asset_id {look_id} is not a '
                                    f'style_world_bible/look asset (kind {kind!r})')
                if not isinstance(required, dict) or look_id not in required:
                    blockers.append(f'{aid}: final visual artifact must pin the active look '
                                    f'{look_id} in required_asset_versions')
                locked(look_id, required.get(look_id) if isinstance(required, dict) else None,
                       f'{aid} (active look)')

        for dep, expected in sorted((artifact.get('dependency_versions') or {}).items()):
            owner = artifacts.get(dep)
            if owner is None:
                blockers.append(f'{aid}: dependency version pins unregistered {dep}')
            elif owner.get('version') != expected:
                blockers.append(f'{aid}: dependency {dep} version drift '
                                f'(pinned {expected}, current {owner.get("version")})')
        visited = {aid}
        stack = list(artifact.get('dependencies', []) or [])
        while stack:
            dep = stack.pop()
            if dep in visited:
                continue
            visited.add(dep)
            owner = artifacts.get(dep)
            if owner is None:
                blockers.append(f'{aid}: dependency {dep} is not registered')
                continue
            if owner.get('status') == 'draft':
                blockers.append(f'{aid}: dependency {dep} is still a draft')
            elif owner.get('status') == 'stale':
                blockers.append(f'{aid}: final depends on stale artifact {dep}')
            for pinned_dep, expected in sorted((owner.get('dependency_versions') or {}).items()):
                upstream = artifacts.get(pinned_dep)
                if upstream is not None and upstream.get('version') != expected:
                    blockers.append(f'{aid}: chain link {dep} has {pinned_dep} version drift '
                                    f'(pinned {expected}, current {upstream.get("version")})')
            stack.extend(owner.get('dependencies', []) or [])

    # Panel-level version pins for checked final boards: panels must reference
    # the exact locked assets their shot uses plus the active LOOK.
    if final_boards:
        shots = {row['id']: row for row in project.get('shots', [])
                 if isinstance(row, dict) and isinstance(row.get('id'), str)}
        for panel in project.get('storyboard', {}).get('panels', []):
            if not isinstance(panel, dict):
                continue
            label = panel.get('id', 'panel')
            avr = panel.get('asset_version_refs')
            if not isinstance(avr, dict) or not avr:
                blockers.append(f'{label}: final panel needs asset_version_refs')
                continue
            required = set(shots.get(panel.get('shot_id'), {}).get('asset_ids') or [])
            if isinstance(look_id, str):
                required.add(look_id)
            for asset_id in sorted(required - set(avr)):
                blockers.append(f'{label}: missing required asset reference {asset_id}')
            for asset_id, expected in avr.items():
                locked(asset_id, expected, label)
    return blockers


def validate_storyboard_extractions(project, base_dir):
    """Validate the current registered sheets and every sheet-derived image.

    The check is shared by image-backed readiness and FINAL package export;
    it validates bytes and pixels rather than trusting the split manifest's
    self-reported hashes.
    """
    errors = []
    if not isinstance(project, dict):
        return ['preproduction extraction: project must be an object']
    declaration = project.get('preproduction')
    if not isinstance(declaration, dict) or declaration.get('mode') != 'image_backed':
        return []
    try:
        base = Path(base_dir).resolve()
    except (OSError, TypeError, ValueError):
        return ['preproduction extraction: a valid --base-dir is required']

    def table(name):
        rows = project.get(name)
        return {row['id']: row for row in rows if isinstance(row, dict)
                and isinstance(row.get('id'), str)} if isinstance(rows, list) else {}

    assets = table('asset_registry')
    artifacts = table('artifacts')
    shots = table('shots')
    storyboard = project.get('storyboard')
    panels = storyboard.get('panels', []) if isinstance(storyboard, dict) else []
    panels = [row for row in panels if isinstance(row, dict)] if isinstance(panels, list) else []
    panels_by_id = {row.get('id'): row for row in panels if isinstance(row.get('id'), str)}
    panel_ids = [row.get('id') for row in panels]
    if any(not isinstance(pid, str) or not pid for pid in panel_ids) \
            or len(set(panel_ids)) != len(panel_ids):
        errors.append('preproduction extraction: canonical panel ids must be unique strings')
    checked_sources = set()

    def safe_file(raw, label):
        if not isinstance(raw, str) or not raw.strip():
            errors.append(f'preproduction {label}: project-relative file path required')
            return None
        rel = Path(raw)
        if rel.is_absolute() or '..' in rel.parts:
            errors.append(f'preproduction {label}: unsafe project-relative path')
            return None
        candidate = base
        try:
            for part in rel.parts:
                candidate = candidate / part
                if candidate.is_symlink() or (hasattr(candidate, 'is_junction')
                                              and candidate.is_junction()):
                    raise ValueError('symbolic link in managed path')
            resolved = candidate.resolve()
            if not resolved.is_relative_to(base) or not resolved.is_file():
                raise ValueError('file missing or outside project root')
            return resolved
        except (OSError, ValueError) as exc:
            errors.append(f'preproduction {label}: {exc}')
            return None

    def digest(path, label):
        if path is None:
            return None
        try:
            return hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as exc:
            errors.append(f'preproduction {label}: cannot hash actual file ({exc})')
            return None


    split_id = declaration.get('storyboard_split_asset_id')
    split_asset = assets.get(split_id) if isinstance(split_id, str) else None
    if split_asset is None or split_asset.get('kind') != 'storyboard_split_manifest' \
            or split_asset.get('status') not in ('available', 'verified'):
        return errors + ['preproduction storyboard split manifest: registered current manifest required']
    split_path = safe_file(split_asset.get('path'), 'storyboard split manifest')
    if split_path is None:
        return errors
    split_sha = digest(split_path, 'storyboard split manifest')
    if split_sha != split_asset.get('sha256'):
        errors.append('preproduction storyboard split manifest: actual bytes do not match registered SHA-256')
    try:
        manifest = json.loads(split_path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, ValueError) as exc:
        return errors + [f'preproduction storyboard split manifest: unreadable ({exc})']
    if not isinstance(manifest, dict) or manifest.get('kind') != 'split-manifest' \
            or manifest.get('derived') is not True or manifest.get('mode') != 'sheet' \
            or manifest.get('project_id') != project.get('project_id'):
        errors.append('preproduction storyboard split manifest: must derive from this project’s produced sheets')

    sheet_ids = declaration.get('storyboard_sheet_artifact_ids')
    if not isinstance(sheet_ids, list) or not sheet_ids \
            or any(not isinstance(aid, str) for aid in sheet_ids) \
            or len(set(sheet_ids)) != len(sheet_ids):
        errors.append('preproduction combined storyboard sheets: unique ordered sheet artifact ids required')
        sheet_ids = []

    expected_sheets = []
    panel_to_sheet = {}
    observed_panel_order = []
    from PIL import Image
    for sheet_index, sheet_id in enumerate(sheet_ids, 1):
        artifact = artifacts.get(sheet_id)
        if artifact is None or artifact.get('type') != 'storyboard_sheet':
            errors.append(f'preproduction sheet {sheet_id}: registered storyboard_sheet artifact required')
            continue
        sheet_asset_ids = artifact.get('asset_ids')
        if not isinstance(sheet_asset_ids, list) or len(sheet_asset_ids) != 1:
            errors.append(f'preproduction sheet {sheet_id}: exactly one registered image asset required')
            continue
        sheet_asset = assets.get(sheet_asset_ids[0])
        if sheet_asset is None or sheet_asset.get('kind') != 'storyboard_sheet' \
                or sheet_asset.get('status') not in ('available', 'verified'):
            errors.append(f'preproduction sheet {sheet_id}: current produced sheet asset required')
            continue
        sheet_path = safe_file(sheet_asset.get('path'), f'sheet {sheet_id}')
        if sheet_path is None:
            continue
        actual_sha = digest(sheet_path, f'sheet {sheet_id}')
        if actual_sha != sheet_asset.get('sha256'):
            errors.append(f'preproduction sheet {sheet_id}: actual bytes do not match registered SHA-256')
        try:
            with Image.open(sheet_path) as image:
                image.load()
                info = dict(image.info)
                size = image.size
        except (OSError, ValueError) as exc:
            errors.append(f'preproduction sheet {sheet_id}: unreadable image ({exc})')
            continue

        def metadata(key, expected_type, default=None):
            raw = info.get(f'storyboard_sheet.{key}')
            try:
                value = json.loads(raw) if raw is not None else default
            except (TypeError, ValueError):
                value = None
            if not isinstance(value, expected_type):
                errors.append(f'preproduction sheet {sheet_id}: malformed storyboard_sheet.{key}')
                return default
            return value

        raw_panel_ids = info.get('storyboard_sheet.panel_ids')
        recorded_panels = raw_panel_ids.split(',') if isinstance(raw_panel_ids, str) else []
        declared_panels = sheet_asset.get('panel_ids')
        if not isinstance(declared_panels, list) or recorded_panels != declared_panels:
            errors.append(f'preproduction sheet {sheet_id}: panel order does not match its registration')
        panel_bounds = metadata('panel_bounds', list, [])
        scene_bounds = metadata('scene_bounds', list, [])
        traceability = metadata('panel_traceability', list, [])
        if [row.get('panel_id') for row in traceability if isinstance(row, dict)] != recorded_panels \
                or len(traceability) != len(recorded_panels):
            errors.append(f'preproduction sheet {sheet_id}: traceability does not cover its panels exactly once')
        source_hashes = metadata('source_sha256', dict, {})
        try:
            metadata_index = int(info.get('storyboard_sheet.sheet_index'))
        except (TypeError, ValueError):
            metadata_index = None
        if metadata_index != sheet_index \
                or (len(sheet_ids) > 1 and sheet_asset.get('sheet_index') != sheet_index):
            errors.append(f'preproduction sheet {sheet_id}: sheet_index order mismatch')
        if sheet_asset.get('sheet_count') is not None \
                and sheet_asset.get('sheet_count') != len(sheet_ids):
            errors.append(f'preproduction sheet {sheet_id}: sheet_count mismatch')
        if len(recorded_panels) > 8 or not recorded_panels:
            errors.append(f'preproduction sheet {sheet_id}: must contain 1 through 8 panels')
        if len(panel_bounds) != len(recorded_panels):
            errors.append(f'preproduction sheet {sheet_id}: panel bounds count mismatch')
        panel_rectangles = []
        for pid, row in zip(recorded_panels, panel_bounds):
            bounds = row.get('bounds') if isinstance(row, dict) else None
            if (not isinstance(row, dict) or row.get('panel_id') != pid
                    or not isinstance(bounds, list) or len(bounds) != 4
                    or any(type(value) is not int for value in bounds)
                    or bounds[0] < 0 or bounds[1] < 0
                    or bounds[0] >= bounds[2] or bounds[1] >= bounds[3]
                    or bounds[2] > size[0] or bounds[3] > size[1]):
                errors.append(f'preproduction sheet {sheet_id}: malformed panel bounds for {pid}')
            else:
                panel_rectangles.append(tuple(bounds))
        for index, bounds in enumerate(panel_rectangles):
            if any(bounds[0] < other[2] and other[0] < bounds[2]
                   and bounds[1] < other[3] and other[1] < bounds[3]
                   for other in panel_rectangles[index + 1:]):
                errors.append(f'preproduction sheet {sheet_id}: panel bounds overlap')
                break
        scene_rectangles = []
        for row in scene_bounds:
            bounds = row.get('bounds') if isinstance(row, dict) else None
            if (not isinstance(row, dict) or not isinstance(row.get('scene_id'), str)
                    or not isinstance(bounds, list) or len(bounds) != 4
                    or any(type(value) is not int for value in bounds)
                    or bounds[0] < 0 or bounds[1] < 0
                    or bounds[0] >= bounds[2] or bounds[1] >= bounds[3]
                    or bounds[2] > size[0] or bounds[3] > size[1]):
                errors.append(f'preproduction sheet {sheet_id}: malformed scene bounds')
            else:
                scene_rectangles.append(tuple(bounds))
        for index, bounds in enumerate(scene_rectangles):
            if any(bounds[0] < other[2] and other[0] < bounds[2]
                   and bounds[1] < other[3] and other[1] < bounds[3]
                   for other in scene_rectangles[index + 1:]):
                errors.append(f'preproduction sheet {sheet_id}: scene bounds overlap')
                break
        for pid in recorded_panels:
            observed_panel_order.append(pid)
            panel_to_sheet.setdefault(pid, sheet_index - 1)
        expected_source_ids = []
        expected_sources = {}
        for pid in recorded_panels:
            panel = panels_by_id.get(pid)
            if panel is None:
                continue
            source_id = panel.get('source_sheet_asset_id') or panel.get('image_asset_id')
            source_asset = assets.get(source_id) if isinstance(source_id, str) else None
            if source_asset is None or source_asset.get('status') not in ('available', 'verified'):
                errors.append(f'preproduction sheet {sheet_id}: current source asset required for {pid}')
                continue
            expected_source_ids.append(source_id)
            expected_sources[source_id] = source_asset.get('sha256')
            if source_id not in checked_sources:
                checked_sources.add(source_id)
                source_path = safe_file(source_asset.get('path'), f'source {source_id}')
                source_sha = digest(source_path, f'source {source_id}')
                if source_sha != source_asset.get('sha256'):
                    errors.append(f'preproduction source {source_id}: actual bytes do not match registered SHA-256')
        if source_hashes != expected_sources:
            errors.append(f'preproduction sheet {sheet_id}: source hashes do not match canonical source assets')
        if sheet_asset.get('source_asset_ids') != expected_source_ids \
                or sheet_asset.get('source_sha256') != expected_sources:
            errors.append(f'preproduction sheet {sheet_id}: registered source mapping is stale')
        expected_sheets.append({
            'artifact_id': sheet_id, 'asset': sheet_asset, 'path': sheet_path,
            'relative': sheet_path.relative_to(base).as_posix(), 'sha256': actual_sha,
            'panel_ids': recorded_panels, 'size': size, 'info': info,
            'panel_bounds': panel_bounds, 'scene_bounds': scene_bounds,
            'traceability': traceability, 'source_hashes': source_hashes,
        })
    if observed_panel_order != panel_ids:
        errors.append('preproduction combined storyboard sheets: every canonical panel must appear once in story order')

    observed_sheets = manifest.get('sheets') if isinstance(manifest, dict) else None
    if not isinstance(observed_sheets, list) or len(observed_sheets) != len(expected_sheets):
        errors.append('preproduction storyboard split manifest: current ordered storyboard sheets mismatch')
        observed_sheets = observed_sheets if isinstance(observed_sheets, list) else []
    for index, expected in enumerate(expected_sheets):
        if index >= len(observed_sheets):
            break
        observed = observed_sheets[index]
        if not isinstance(observed, dict) \
                or Path(str(observed.get('path', ''))).as_posix() != expected['relative'] \
                or observed.get('sha256') != expected['sha256'] \
                or observed.get('panel_ids') != expected['panel_ids'] \
                or observed.get('sheet_index') != index + 1:
            errors.append('preproduction storyboard split manifest: current ordered storyboard sheets mismatch')
            break

    entries = manifest.get('entries') if isinstance(manifest, dict) else None
    entries = entries if isinstance(entries, list) else []
    if [entry.get('panel_id') for entry in entries if isinstance(entry, dict)] != panel_ids:
        errors.append('preproduction storyboard split manifest: canonical panel order/coverage mismatch')
    if len(entries) != len(panel_ids):
        errors.append('preproduction storyboard split manifest: each panel needs exactly one entry')

    registered_paths = {}
    for asset in assets.values():
        raw = asset.get('path')
        if isinstance(raw, str) and raw.strip() and asset.get('status') in ('available', 'verified'):
            registered_paths.setdefault(Path(raw).as_posix().casefold(), []).append(asset)
    used_outputs = set()

    def registered_output(path, declared_sha, label):
        relative = path.relative_to(base).as_posix().casefold()
        if relative in used_outputs:
            errors.append(f'preproduction {label}: panel/scene outputs must use distinct files')
        used_outputs.add(relative)
        rows = registered_paths.get(relative, [])
        actual_sha = digest(path, label)
        if len(rows) != 1 or rows[0].get('kind') != 'image' \
                or rows[0].get('sha256') != declared_sha or actual_sha != declared_sha:
            errors.append(f'preproduction {label}: extracted file registration/hash mismatch')

    def output_path(file_name, label):
        if not isinstance(file_name, str) or not file_name.strip():
            errors.append(f'preproduction {label}: output file required')
            return None
        rel = Path(file_name)
        if rel.is_absolute() or '..' in rel.parts:
            errors.append(f'preproduction {label}: unsafe output path')
            return None
        # The splitter stores output paths relative to the manifest directory.
        return safe_file((split_path.parent / rel).relative_to(base).as_posix(), label)

    for index, entry in enumerate(entries):
        if not isinstance(entry, dict) or index >= len(panels):
            errors.append('preproduction storyboard split manifest: malformed panel entry')
            continue
        panel = panels[index]
        pid = panel.get('id')
        shot = shots.get(panel.get('shot_id'))
        scene_id = shot.get('scene_id') if isinstance(shot, dict) else None
        source_id = panel.get('source_sheet_asset_id') or panel.get('image_asset_id')
        source = assets.get(source_id) if isinstance(source_id, str) else None
        sheet_index = panel_to_sheet.get(pid)
        sheet = expected_sheets[sheet_index] if isinstance(sheet_index, int) \
            and sheet_index < len(expected_sheets) else None
        if (entry.get('index') != index + 1 or entry.get('shot_id') != panel.get('shot_id')
                or entry.get('scene_id') != scene_id
                or entry.get('source_asset_id') != source_id
                or entry.get('source_asset_version') != (source or {}).get('version')
                or entry.get('source_sha256') != (source or {}).get('sha256')):
            errors.append(f'preproduction split panel {pid}: source/pin/scene mismatch')
        sheet_path_raw = entry.get('sheet_path')
        if sheet is None or not isinstance(sheet_path_raw, str) \
                or Path(sheet_path_raw).as_posix() != (sheet['relative'] if sheet else None) \
                or entry.get('sheet_index') != (sheet_index + 1 if isinstance(sheet_index, int) else None):
            errors.append(f'preproduction split panel {pid}: sheet_path does not match current registered sheet')
        bounds = entry.get('bounds')
        if not isinstance(bounds, list) or len(bounds) != 4 \
                or any(type(value) is not int for value in bounds):
            errors.append(f'preproduction split panel {pid}: valid bounds required')
            continue
        if sheet is None:
            continue
        recorded_bounds = [row for row in sheet['panel_bounds']
                           if isinstance(row, dict) and row.get('panel_id') == pid]
        trace = [row for row in sheet['traceability']
                 if isinstance(row, dict) and row.get('panel_id') == pid]
        if len(recorded_bounds) != 1 or recorded_bounds[0].get('bounds') != bounds \
                or len(trace) != 1:
            errors.append(f'preproduction split panel {pid}: provenance or bounds mismatch')
            continue
        provenance = trace[0]
        if (provenance.get('source_asset_id') != source_id
                or provenance.get('source_asset_version') != (source or {}).get('version')
                or provenance.get('source_sha256') != (source or {}).get('sha256')
                or provenance.get('asset_version_refs') != panel.get('asset_version_refs', {})
                or provenance.get('shot_id') != panel.get('shot_id')
                or provenance.get('scene_id') != scene_id):
            errors.append(f'preproduction split panel {pid}: sheet provenance does not match canonical panel')
        l, t, r, b = bounds
        matching_bands = [row for row in sheet['scene_bounds']
                          if isinstance(row, dict) and row.get('scene_id') == scene_id]
        if not any(isinstance(row.get('bounds'), list) and len(row['bounds']) == 4
                   and all(type(value) is int for value in row['bounds'])
                   and row['bounds'][0] <= l and row['bounds'][1] <= t
                   and row['bounds'][2] >= r and row['bounds'][3] >= b
                   for row in matching_bands):
            errors.append(f'preproduction split panel {pid}: bounds escape its scene band')
        output = output_path(entry.get('file'), f'split panel {pid}')
        if output is None:
            continue
        registered_output(output, entry.get('sha256'), f'split panel {pid}')
        try:
            with Image.open(sheet['path']) as sheet_image, Image.open(output) as extracted:
                with sheet_image.crop(tuple(bounds)) as crop:
                    expected_rgba = crop.convert('RGBA')
                    actual_rgba = extracted.convert('RGBA')
                    try:
                        if expected_rgba.size != actual_rgba.size \
                                or expected_rgba.tobytes() != actual_rgba.tobytes():
                            errors.append(f'preproduction split panel pixels do not match source sheet: {pid}')
                    finally:
                        expected_rgba.close()
                        actual_rgba.close()
        except (OSError, ValueError) as exc:
            errors.append(f'preproduction split panel {pid}: cannot compare actual pixels ({exc})')

    scene_entries = manifest.get('scenes') if isinstance(manifest, dict) else None
    scene_entries = scene_entries if isinstance(scene_entries, list) else []
    scenes_order = []
    for panel in panels:
        shot = shots.get(panel.get('shot_id'))
        scene_id = shot.get('scene_id') if isinstance(shot, dict) else None
        if not isinstance(scene_id, str) or not scene_id:
            errors.append(f'preproduction panel {panel.get("id")}: valid scene_id required')
        elif scene_id not in scenes_order:
            scenes_order.append(scene_id)
    if [row.get('scene_id') for row in scene_entries if isinstance(row, dict)] != scenes_order:
        errors.append('preproduction storyboard split manifest: canonical scene order/coverage mismatch')
    if len(scene_entries) != len(scenes_order):
        errors.append('preproduction storyboard split manifest: one overview per scene required')

    for scene in scene_entries:
        if not isinstance(scene, dict):
            errors.append('preproduction storyboard split manifest: malformed scene entry')
            continue
        scene_id = scene.get('scene_id')
        expected_panel_ids = [panel.get('id') for panel in panels
                              if shots.get(panel.get('shot_id'), {}).get('scene_id') == scene_id]
        if scene.get('panel_ids') != expected_panel_ids:
            errors.append(f'preproduction split scene {scene_id}: panel membership mismatch')
        bands = []
        expected_sources = []
        for sheet in expected_sheets:
            for row in sheet['scene_bounds']:
                if isinstance(row, dict) and row.get('scene_id') == scene_id \
                        and isinstance(row.get('bounds'), list) and len(row['bounds']) == 4:
                    bands.append((sheet['path'], row['bounds']))
                    expected_sources.append(sheet['relative'])
        if not bands or scene.get('band_sources') != expected_sources:
            errors.append(f'preproduction split scene {scene_id}: band source order mismatch')
        output = output_path(scene.get('file'), f'split scene {scene_id}')
        if output is None:
            continue
        registered_output(output, scene.get('sha256'), f'split scene {scene_id}')
        crops = []
        expected_image = None
        try:
            for sheet_path, bounds in bands:
                with Image.open(sheet_path) as source_image:
                    with source_image.crop(tuple(bounds)) as crop:
                        with crop.convert('RGB') as rgb_crop:
                            crops.append(rgb_crop.convert('RGBA'))
            if not crops:
                continue
            expected_image = Image.new('RGBA',
                                       (max(crop.width for crop in crops),
                                        sum(crop.height for crop in crops)),
                                       (255, 255, 255, 255))
            offset = 0
            for crop in crops:
                expected_image.paste(crop, (0, offset))
                offset += crop.height
            with Image.open(output) as extracted:
                actual_rgba = extracted.convert('RGBA')
                try:
                    if expected_image.size != actual_rgba.size \
                            or expected_image.tobytes() != actual_rgba.tobytes():
                        errors.append(f'preproduction split scene pixels do not match source bands: {scene_id}')
                finally:
                    actual_rgba.close()
        except (OSError, ValueError) as exc:
            errors.append(f'preproduction split scene {scene_id}: cannot compare actual pixels ({exc})')
        finally:
            for crop in crops:
                crop.close()
            if expected_image is not None:
                expected_image.close()
    return errors
