"""Join staged scene/timing and TTS outputs into the renderer's input contract.

No generation, retiming, copying, network, or caller-chosen render job ID.
"""
from __future__ import annotations
import math
from pathlib import Path
from adapters.caption_style import load_default_caption_style
from adapters.render_ffmpeg import _sha256_file, _validate_manifest, RenderError


def assemble_render_manifest(shot_sequence, tts_result, *, artifact_root):
    if not isinstance(shot_sequence, list) or not shot_sequence or not isinstance(tts_result, dict):
        raise RenderError('render_handoff_incomplete')
    duration = tts_result.get('duration_seconds')
    if (isinstance(duration, bool) or not isinstance(duration, (int, float))
            or not math.isfinite(duration) or not 0 < duration <= 180):
        raise RenderError('render_handoff_duration_invalid')
    scenes = []
    for shot in shot_sequence:
        if not isinstance(shot, dict):
            raise RenderError('render_handoff_shot_invalid')
        scenes.append({'image_path': shot.get('scene_image'),
                       'start_seconds': shot.get('start_seconds'), 'end_seconds': shot.get('end_seconds')})
    captions = tts_result.get('captions')
    if not isinstance(captions, list) or not captions:
        raise RenderError('render_handoff_captions_missing')
    cues = []
    for cue in captions:
        if not isinstance(cue, dict):
            raise RenderError('render_handoff_cue_invalid')
        cues.append({k: cue.get(k) for k in ('start_seconds', 'end_seconds', 'text')})
    manifest = {'scenes': scenes, 'audio_path': tts_result.get('artifact_path'), 'cues': cues,
                'caption_style': load_default_caption_style()}
    # Validation-only ID is never returned; the claimed DB job ID is added by main.py.
    _, validated_scenes, _, validated_cues, _ = _validate_manifest(
        {**manifest, 'job_id': 'handoff-validation'}, Path(artifact_root))
    if abs(validated_scenes[-1]['end'] - duration) > 0.05 or validated_cues[-1]['end'] > duration + 0.001:
        raise RenderError('render_handoff_timeline_mismatch')
    digests = [_sha256_file(scene['path']) for scene in validated_scenes]
    if len(set(digests)) != len(digests):
        raise RenderError('render_handoff_reused_scene')
    return manifest
