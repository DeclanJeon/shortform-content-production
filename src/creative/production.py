"""Pure prompt construction and cue-aligned production utilities."""
from __future__ import annotations

import json
import math
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from .contract import validate_plan


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _items(plan, key):
    value = plan.get(key, [])
    return value if isinstance(value, list) else []


def _id(item, prefix, index):
    return str(item.get("id", item.get("character_id" if prefix == "CH" else "shot_id", f"{prefix}{index:02d}")))


def _segment_text(segment):
    for key in ("narration", "text", "voiceover", "script"):
        if isinstance(segment.get(key), str):
            return segment[key]
    return ""


def _validate(plan):
    validate_plan(plan)


def build_asset_specs(plan) -> list[dict]:
    _validate(plan)
    chars = _items(plan, "characters")
    shots = _items(plan, "shots")
    style = plan['visual_style']
    specs = []
    for i, character in enumerate(chars, 1):
        cid = _id(character, "CH", i)
        dna = character.get("dna", character)
        prompt = ("Create an original Korean hand-painted 2D storybook chibi character identity sheet, landscape on neutral cream, exactly seven views. "
                  "Keep locked cast proportions, wardrobe and palette. No franchise or real-person references; no words, logos, watermarks, or embedded captions. "
                  "No scene bank or collages. Draw only the identity artwork; no page template, header, title or UI.\n"
                  f"AUTHORITATIVE CHARACTER JSON DNA: {_json(dna)}\nSHARED STYLE JSON: {_json(style)}")
        specs.append({"asset_id": cid, "kind": "character_image", "relative_path": f"02_MASTER_ASSETS/CHARACTERS/{cid}-identity-v1.png", "prompt": prompt, "reference_ids": []})
    for i, shot in enumerate(shots, 1):
        sid = _id(shot, "SH", i)
        selected = shot.get("character_ids", shot.get("characters", []))
        refs = [x if isinstance(x, str) else x.get("id", x.get("character_id")) for x in selected]
        refs = [str(x) for x in refs if x]
        dna = [c.get("dna", c) for c in chars if _id(c, "CH", chars.index(c)+1) in refs]
        segment = shot.get("segment_id", shot.get("segment", ""))
        prompt = ("Create one original Korean hand-painted 2D storybook chibi illustration, a single clean portrait 9:16 scene. "
                  "Keep the locked cast proportions, wardrobe and palette; depict the unique action and framing specified. No franchise or real-person references; no words, logos, watermarks, or embedded captions. "
                  "Draw only the scene artwork, filling the image; the supplied fixed page template, title and captions are composited separately. Compose for a centered 3:4 panel crop of the portrait source: keep essential faces, reacting hands and causal props within the central 75 percent of image height, without blank bands. No page frame, interface, header, title or blank UI bands. No scene bank or collages. Special unseen extras prohibited; only generic unidentifiable background figures when explicitly specified.\n"
                  f"AUTHORITATIVE CHARACTER JSON DNA: {_json(dna)}\nSHARED STYLE JSON: {_json(style)}\n"
                  f"STAGE: {_json(shot.get('stage', ''))}\nACTION: {_json(shot.get('action', ''))}\nSHOT_SIZE: {_json(shot.get('shot_size', ''))}\nSEGMENT: {_json(segment)}")
        context = None
        if plan['schema_version'] in ('2.0', '3.0'):
            context = {'text':plan['narration_segments'][i-1]['text'],
                'previous_text':plan['narration_segments'][i-2]['text'] if i > 1 else '',
                'next_text':plan['narration_segments'][i]['text'] if i < len(shots) else '',
                'story':plan['narrative']['master_text'], 'stage':shot['stage'],
                'performance':shot['performance'], 'delivery':plan['narrative']['delivery']}
            prompt += ('\nNARRATIVE_CONTEXT: '+_json(context)+'\nShow one drawable moment of the '
                'specified reaction, not simultaneous before/after poses. Identity references lock appearance, '
                'not expression, pose or framing. Emotional proximity and readable receiving reactions are '
                'functional requirements. Preserve the stated locations and who is physically present; '
                'do not put remote callers together. Do not print narration or acting instructions in the image.')
        specs.append({"asset_id": sid, "kind": "scene_image", "relative_path": f"03_SCENE_PACKS/{sid}.png", "prompt": prompt, "reference_ids": refs,
                      **({'narrative_context':context} if context is not None else {})})
    return specs


def _norm(text):
    return "".join(c for c in text.casefold() if c.isalnum())


def align_shots(plan, tts_result, scene_paths) -> list[dict]:
    _validate(plan)
    shots, segments = plan['shots'], plan['narration_segments']
    duration = tts_result.get("duration_seconds")
    if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration <= 0 or duration > 180:
        raise ValueError("invalid_tts_duration")
    duration = float(duration)
    captions = tts_result.get("captions")
    if not isinstance(captions, list) or not captions:
        raise ValueError("invalid_tts_captions")
    if not isinstance(scene_paths, dict) or set(scene_paths) != {_id(s, "SH", i) for i, s in enumerate(shots, 1)}:
        raise ValueError("scene_path_mapping_mismatch")
    paths = list(scene_paths.values())
    if any(not isinstance(p, str) or not os.path.isabs(p) for p in paths) or len(set(paths)) != len(paths):
        raise ValueError("invalid_scene_paths")
    narration = "".join(_segment_text(s) for s in segments)
    caption_text = ""
    cues, previous = [], 0.0
    for c in captions:
        if not isinstance(c,dict):
            raise ValueError('invalid_tts_captions')
        text, start, end = c.get("text"), c.get("start_seconds"), c.get("end_seconds")
        if not isinstance(text, str) or any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in (start, end)) or start < 0 or end <= start or start < previous or end > duration + .001:
            raise ValueError("invalid_tts_captions")
        previous = end
        caption_text += text
        cues.append((float(start), float(end), text))
    if _norm(narration) != _norm(caption_text):
        raise ValueError("caption_text_mismatch")
    total_chars = sum(len(_norm(t)) for _, _, t in cues)
    seg_lengths = [len(_norm(_segment_text(s))) for s in segments]
    desired = [0.0]
    cumulative = 0
    for length in seg_lengths[:-1]:
        cumulative += length
        target = cumulative / max(1, total_chars)
        pos = target * total_chars
        acc = 0
        estimate = duration * target
        for a, b, text in cues:
            if pos <= acc + len(_norm(text)):
                fraction = (pos - acc) / max(1, len(_norm(text)))
                estimate = a + fraction * (b-a)
                break
            acc += len(_norm(text))
        desired.append(estimate)
    n = len(shots)
    end_frame = round(duration * 30)
    boundaries = {0, end_frame}
    for a, b, _ in cues:
        boundaries.add(round(a*30)); boundaries.add(round(b*30))
    bounds = sorted(x for x in boundaries if 0 <= x <= end_frame)
    # Keep cue-derived cuts without compressing speech to the old four-second estimate.
    max_shot_frames = max(120, math.ceil(duration / n * 30) + 30)
    dp = {(0, 0): (0.0, [])}
    for k in range(1, n):
        for frame in bounds:
            best = None
            for prev in bounds:
                state = dp.get((k-1, prev))
                if state and 1 <= frame-prev <= max_shot_frames:
                    cost = state[0] + (frame/30-desired[k])**2
                    if best is None or cost < best[0]: best = (cost, state[1]+[frame])
            if best: dp[(k, frame)] = best
    best = None
    for prev in bounds:
        state = dp.get((n-1, prev))
        if state and 1 <= end_frame-prev <= max_shot_frames and (best is None or state[0] < best[0]): best = state
    if best is None:
        raise ValueError("cue_aligned_timeline_unavailable")
    frames = [0] + best[1] + [end_frame]
    return [{"shot_id": _id(s, "SH", i), "segment_id": s.get("segment_id", s.get("segment", "")), "scene_image": scene_paths[_id(s, "SH", i)], "start_seconds": frames[i-1]/30, "end_seconds": frames[i]/30, "timing_basis": "actual_tts_caption_boundaries_30fps"} for i, s in enumerate(shots, 1)]


def materialize_plan(plan, project_root, *, version=1) -> list[str]:
    if type(version) is not int or not 1<=version<=20:raise ValueError('invalid_plan_version')
    specs = build_asset_specs(plan)
    segments = plan['narration_segments']
    root = Path(project_root).resolve(strict=True)
    if not (root / "project.json").is_file():
        raise ValueError("project_not_initialized")
    narration = plan['narrative']['master_text'] if plan['schema_version'] in ('2.0', '3.0') else '\n'.join(_segment_text(s) for s in segments)
    synopsis = ['# '+plan['title'], '', plan['synopsis'], '', '## Beat source locators']
    for beat in plan['beats']:
        synopsis.extend(['', f"## {beat['id']}", beat['summary'], f"Emotion: {beat.get('emotion','N/A')}"])
    style_bible = ('# Proposed style-world bible\n\n'
                   'This generated visual direction is a proposal, not an approved or verified LOOK asset.\n\n'
                   +_json(plan['visual_style'])+'\n')
    payloads = {
        f"00_MANIFEST/creative-plan-v{version}.json": _json(plan)+"\n",
        f"07_AUDIO/narration-v{version}.txt": narration+"\n",
        f"00_MANIFEST/asset-specs-v{version}.json": _json(specs)+"\n",
        f"02_STORY/synopsis-v{version}.md": '\n'.join(synopsis)+'\n',
        f"04_CHARACTER_BIBLE/style-world-bible-v{version}.md": style_bible,
    }
    for character in plan['characters']:
        cid=character['id']
        persona=('# '+cid+' — '+character['name']+'\n\n'
                 'Generated persona/SSOT proposal; human acceptance is not claimed.\n\n'
                 +_json(character)+'\n')
        payloads[f"04_CHARACTER_BIBLE/{cid}-v{version}.md"]=persona
    for spec in specs:
        payloads[f"06_GENERATION_SPECS/{spec['asset_id']}-prompt-v{version}.txt"] = spec["prompt"]+"\n"
    # Validate every destination before writing any file.
    destinations = {}
    for rel, content in payloads.items():
        path = root / rel
        if path.is_symlink() or not path.resolve().is_relative_to(root) or not path.parent.resolve().is_relative_to(root):
            raise ValueError("unsafe_project_path")
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.read_text(encoding="utf-8") != content:
            raise FileExistsError(rel)
        destinations[rel] = (path, content)
    written = []
    for rel, (path, content) in destinations.items():
        if not path.exists():
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(content)
                    f.flush()
                    os.fsync(f.fileno())
            except FileExistsError:
                if path.read_text(encoding="utf-8") != content: raise
        written.append(rel)
    return written
