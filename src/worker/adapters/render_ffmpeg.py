"""Local FFmpeg renderer, dispatched by worker/main.py only for a staged local manifest.

This source implementation is not deployed, and current SF08 does not yet produce the full render manifest.

Manifest contract (all paths are relative to ``artifact_root`` unless absolute):

    {
      "job_id": "job-123",
      "scenes": [
        {"image_path": "staged/scene-1.png", "start_seconds": 0, "end_seconds": 4}
      ],
      "audio_path": "staged/narration.wav",
      "cues": [
        {"start_seconds": 0.2, "end_seconds": 2.1, "text": "Caption text"}
      ],
      "caption_style": <the exact caption_style_v10.json object>
    }

Each scene is a 1080x1920 PNG or JPEG displayed for its declared interval. Scene
intervals must cover the narration timeline contiguously. Audio is WAV or MP3.
The output and a temporary working directory are created beneath
``artifact_root/<job_id>/``. ``render()`` returns a local MP4 path, SHA-256,
size, duration, dimensions, codec, and validated cue metadata. Captions are
wrapped before rendering without changing cue timestamps.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
import tempfile
from fractions import Fraction
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from adapters.caption_style import validate_render_caption_style


class RenderError(RuntimeError):
    """A manifest, FFmpeg operation, or rendered artifact failed validation."""


_VIDEO_CODEC = "h264"
_AUDIO_CODECS = {"pcm_s16le", "pcm_s24le", "pcm_f32le", "mp3"}
_IMAGE_CODECS = {"png", "mjpeg"}
_JOB_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}\Z")
_SHA256_CHUNK_SIZE = 1024 * 1024


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(_SHA256_CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def _seconds(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RenderError(f"{field}_must_be_numeric_seconds")
    try:
        result = float(value)
    except OverflowError as exc:
        raise RenderError(f"{field}_must_be_finite") from exc
    if not math.isfinite(result):
        raise RenderError(f"{field}_must_be_finite")
    return result


def _probe_seconds(value: Any, field: str) -> float:
    if isinstance(value, str) and re.fullmatch(r"-?(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)", value):
        value = float(value)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RenderError(f"{field}_must_be_a_valid_probe_duration")
    try:
        result = float(value)
    except OverflowError as exc:
        raise RenderError(f"{field}_must_be_a_valid_probe_duration") from exc
    if not math.isfinite(result):
        raise RenderError(f"{field}_must_be_a_valid_probe_duration")
    return result


def _subtitles_filter_path(path: Path) -> str:
    escaped = str(path)
    for char in ("\\", "'", ":", ",", "[", "]", ";"):
        escaped = escaped.replace(char, "\\" + char)
    return f"'{escaped}'"


def _validate_output_info(info: dict[str, Any], expected_duration: float, canvas: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], float]:
    streams = info.get("streams")
    if not isinstance(streams, list):
        raise RenderError("rendered_manifest_validation_failed")
    videos = [item for item in streams if isinstance(item, dict) and item.get("codec_type") == "video"]
    audios = [item for item in streams if isinstance(item, dict) and item.get("codec_type") == "audio"]
    if len(videos) != 1 or len(audios) != 1:
        raise RenderError("rendered_manifest_validation_failed")
    video, audio = videos[0], audios[0]
    if (video.get("codec_name") != _VIDEO_CODEC or audio.get("codec_name") != "aac"
            or (video.get("width"), video.get("height")) != (canvas["width_px"], canvas["height_px"])):
        raise RenderError("rendered_manifest_validation_failed")
    try:
        frame_rate = Fraction(video.get("avg_frame_rate", "0/0"))
    except (ValueError, ZeroDivisionError, TypeError):
        raise RenderError("rendered_manifest_validation_failed") from None
    if frame_rate != 30:
        raise RenderError("rendered_manifest_validation_failed")
    format_info = info.get("format")
    if not isinstance(format_info, dict):
        raise RenderError("rendered_manifest_validation_failed")
    duration = _probe_seconds(format_info.get("duration"), "rendered_duration")
    # AAC packet padding may extend the MP4 by a few milliseconds.
    if not 0 < duration <= 180 or abs(duration - expected_duration) > 0.05:
        raise RenderError("rendered_duration_mismatch")
    return video, audio, duration


def _local_file(root: Path, value: Any, field: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise RenderError(f"{field}_must_be_a_path")
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc:
        raise RenderError(f"{field}_must_be_local")
    raw = Path(value)
    if ".." in raw.parts:
        raise RenderError(f"{field}_path_traversal")
    candidate = raw if raw.is_absolute() else root / raw
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise RenderError(f"{field}_outside_artifact_root_or_missing") from exc
    if not resolved.is_file() or resolved.stat().st_size == 0:
        raise RenderError(f"{field}_must_be_a_nonempty_file")
    return resolved


def _wrap_text(text: str, max_width: int) -> str:
    """Balance two lines against a conservative estimate of rendered width."""
    words = text.split()
    if not words:
        raise RenderError("caption_text_must_not_be_empty")

    def width(word: str) -> int:
        # CJK characters commonly occupy one full em; other characters use half.
        return sum(2 if ord(char) > 0x2E7F else 1 for char in word) * 40

    widths = [width(word) for word in words]
    if any(word_width > max_width for word_width in widths):
        raise RenderError("caption_word_exceeds_max_line_width")
    space_width = 40
    full_width = sum(widths) + space_width * (len(words) - 1)
    if full_width <= max_width:
        return " ".join(words)
    candidates = []
    for split in range(1, len(words)):
        first = sum(widths[:split]) + space_width * (split - 1)
        second = sum(widths[split:]) + space_width * (len(words) - split - 1)
        if first <= max_width and second <= max_width:
            candidates.append((abs(first - second), split))
    if not candidates:
        raise RenderError("caption_exceeds_two_lines")
    _, split = min(candidates)
    return " ".join(words[:split]) + "\\N" + " ".join(words[split:])


def _run(args: list[str]) -> bytes:
    try:
        completed = subprocess.run(
            args, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            shell=False,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RenderError("ffmpeg_tool_failed") from exc
    return completed.stdout


def _probe(path: Path, *, streams: bool = False) -> dict[str, Any]:
    args = ["ffprobe", "-v", "error", "-show_format", "-of", "json"]
    if streams:
        args.extend(["-show_streams"])
    args.append(str(path))
    try:
        result = json.loads(_run(args))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RenderError("ffprobe_returned_invalid_json") from exc
    if not isinstance(result, dict):
        raise RenderError("ffprobe_returned_invalid_manifest")
    return result


def _input_info(path: Path, kind: str, style: dict[str, Any]) -> dict[str, Any]:
    result = _probe(path, streams=True)
    streams = result.get("streams")
    if not isinstance(streams, list) or len(streams) != 1:
        raise RenderError(f"{kind}_must_have_one_stream")
    stream = streams[0]
    codec = stream.get("codec_name")
    if kind == "image":
        canvas = style["canvas"]
        if codec not in _IMAGE_CODECS or (stream.get("width"), stream.get("height")) != (canvas["width_px"], canvas["height_px"]):
            raise RenderError("unsupported_image_codec_or_dimensions")
    elif codec not in _AUDIO_CODECS:
        raise RenderError("unsupported_audio_codec")
    return stream


def _validate_manifest(manifest: Any, artifact_root: Path) -> tuple[str, list[dict[str, Any]], Path, list[dict[str, Any]], dict[str, Any]]:
    if not isinstance(manifest, dict):
        raise RenderError("manifest_must_be_an_object")
    job_id = manifest.get("job_id")
    if not isinstance(job_id, str) or not _JOB_ID.fullmatch(job_id):
        raise RenderError("job_id_invalid")
    try:
        root = artifact_root.resolve(strict=True)
    except OSError as exc:
        raise RenderError("artifact_root_must_exist") from exc
    if not root.is_dir():
        raise RenderError("artifact_root_must_be_a_directory")
    try:
        style = validate_render_caption_style(manifest.get("caption_style"))
    except ValueError as exc:
        raise RenderError("caption_style_must_match_approved_preset") from exc

    scenes_value = manifest.get("scenes")
    if not isinstance(scenes_value, list) or not scenes_value:
        raise RenderError("scenes_must_be_a_nonempty_list")
    scenes: list[dict[str, Any]] = []
    prior_end = 0.0
    for index, scene in enumerate(scenes_value):
        if not isinstance(scene, dict):
            raise RenderError("scene_must_be_an_object")
        image = _local_file(root, scene.get("image_path"), f"scene_{index}_image")
        start = _seconds(scene.get("start_seconds"), f"scene_{index}_start")
        end = _seconds(scene.get("end_seconds"), f"scene_{index}_end")
        if start < 0 or end <= start or end > 180 or abs(start - prior_end) > 0.001:
            raise RenderError("scene_timing_must_be_contiguous_and_positive")
        move=scene.get('camera_move','static')
        if move not in ('static','push','pull','pan_left','pan_right'):
            raise RenderError('scene_camera_move_invalid')
        scenes.append({"path": image, "start": start, "end": end, 'camera_move':move})
        prior_end = end

    audio = _local_file(root, manifest.get("audio_path"), "audio")
    cues_value = manifest.get("cues")
    if not isinstance(cues_value, list) or not cues_value:
        raise RenderError("cues_must_be_a_nonempty_list")
    cues: list[dict[str, Any]] = []
    prior_start = -1.0
    prior_end = 0.0
    for index, cue in enumerate(cues_value):
        if not isinstance(cue, dict):
            raise RenderError("cue_must_be_an_object")
        start = _seconds(cue.get("start_seconds"), f"cue_{index}_start")
        end = _seconds(cue.get("end_seconds"), f"cue_{index}_end")
        text = cue.get("text")
        if not isinstance(text, str) or not text.strip() or start < 0 or end <= start or start < prior_start or start < prior_end:
            raise RenderError("cue_timing_or_text_invalid")
        prior_start = start
        prior_end = end
        wrapped = _wrap_text(text.strip(), style["safe_area"]["max_line_width_px"])
        cues.append({"start": start, "end": end, "text": wrapped, "source_text": text.strip()})

    return job_id, scenes, audio, cues, style


def _write_ass(path: Path, cues: list[dict[str, Any]], style: dict[str, Any]) -> None:
    font = style["font"]
    backplate = style["backplate"]
    # ASS alpha is inverted; convert the pinned decimal opacity to 8-bit alpha.
    alpha = round((1.0 - float(backplate["opacity"])) * 255)
    primary = "&H00FFFFFF"
    outline = "&H00000000"
    back = f"&H{alpha:02X}000000"
    canvas = style["canvas"]
    safe_area = style["safe_area"]
    placement = style["placement"]
    margin_left = placement["anchor_x_px"] - safe_area["max_line_width_px"] // 2
    margin_right = canvas["width_px"] - placement["anchor_x_px"] - safe_area["max_line_width_px"] // 2
    ass = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {canvas['width_px']}", f"PlayResY: {canvas['height_px']}",
        "WrapStyle: 2", "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]", "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Default,{font['family']},{font['size_px']},{primary},{primary},{outline},{back},-1,0,3,{backplate['padding_px']},0,2,{margin_left},{margin_right},{canvas['height_px'] - placement['anchor_y_px']},1",
        "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for cue in cues:
        start = _ass_time(cue["start"])
        end = _ass_time(cue["end"])
        text = cue["text"].replace("{", "\\{").replace("}", "\\}")
        ass.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}")
    path.write_text("\n".join(ass) + "\n", encoding="utf-8")


def _ass_time(value: float) -> str:
    centiseconds = round(value * 100)
    hours, remainder = divmod(centiseconds, 360000)
    minutes, remainder = divmod(remainder, 6000)
    seconds, cs = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{seconds:02d}.{cs:02d}"


def render(manifest: dict[str, Any], *, artifact_root: str | os.PathLike[str]) -> dict[str, Any]:
    """Render and validate a local MP4. No network or worker-process integration occurs."""
    root = Path(artifact_root)
    job_id, scenes, audio, cues, style = _validate_manifest(manifest, root)
    if "listening_edit" in manifest:
        from .listening_audio import prepare
        source_info = _input_info(audio, "audio", style)
        source_duration = _probe_seconds(source_info.get("duration"), "source_audio_duration")
        if abs(source_duration-manifest["listening_edit"]["timeline"]["source_duration_seconds"]) > .001:
            raise RenderError("listener_source_duration_mismatch")
        job_dir = root.resolve()/job_id
        if job_dir.is_symlink() or not job_dir.resolve().is_relative_to(root.resolve()):
            raise RenderError("job_directory_outside_artifact_root")
        audio = prepare(audio, job_dir, manifest["listening_edit"])
    audio_info = _input_info(audio, "audio", style)
    audio_duration = _probe_seconds(audio_info.get("duration"), "audio_duration")
    if not 0 < audio_duration <= 180 or abs(scenes[-1]["end"] - audio_duration) > 0.05:
        raise RenderError("scene_timeline_must_match_audio_duration")
    for scene in scenes:
        _input_info(scene["path"], "image", style)
    if cues[-1]["end"] > audio_duration + 0.001:
        raise RenderError("cue_exceeds_audio_duration")

    job_dir = root.resolve() / job_id
    job_dir.mkdir(mode=0o700, exist_ok=True)
    if job_dir.is_symlink() or not job_dir.resolve().is_relative_to(root.resolve()):
        raise RenderError("job_directory_outside_artifact_root")
    final_path = job_dir / "render.mp4"
    with tempfile.TemporaryDirectory(prefix=".render-", dir=job_dir) as temp_name:
        temp = Path(temp_name)
        ass_path = temp / "captions.ass"
        output = temp / "render.mp4"
        _write_ass(ass_path, cues, style)
        inputs: list[str] = []
        filters: list[str] = []
        for index, scene in enumerate(scenes):
            seconds=scene['end']-scene['start'];frames=max(1,round(seconds*30));denom=max(1,frames-1)
            inputs.extend(['-threads','1','-loop','1','-framerate','30','-t',f'{seconds:.6f}','-i',str(scene['path'])])
            move=scene['camera_move'];motion=''
            if move!='static':
                zoom=f'1+0.025*on/{denom}' if move=='push' else f'1.025-0.025*on/{denom}' if move=='pull' else '1.025'
                x=f'(iw-iw/zoom)*on/{denom}' if move=='pan_right' else f'(iw-iw/zoom)*(1-on/{denom})' if move=='pan_left' else '(iw-iw/zoom)/2'
                motion=f"zoompan=z='{zoom}':x='{x}':y='(ih-ih/zoom)/2':d=1:s=1080x1920:fps=30,"
            filters.append(f"[{index}:v]{motion}fps=30,trim=duration={seconds:.6f},setpts=PTS-STARTPTS,setsar=1[v{index}]")
        concat_inputs = "".join(f"[v{index}]" for index in range(len(scenes)))
        filters.append(f"{concat_inputs}concat=n={len(scenes)}:v=1:a=0,subtitles={_subtitles_filter_path(ass_path)}[vout]")
        audio_index = len(scenes)
        args = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", '-filter_complex_threads','1']
        args.extend(inputs)
        args.extend(["-i", str(audio), "-filter_complex", ";".join(filters), "-map", "[vout]", "-map", f"{audio_index}:a:0", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30", "-c:a", "aac", '-af','adeclip,alimiter=limit=0.85:level=false', '-threads','2', "-movflags", "+faststart", "-shortest", str(output)])
        _run(args)
        if not output.is_file() or output.stat().st_size == 0:
            raise RenderError("ffmpeg_output_missing_or_empty")
        output_info = _probe(output, streams=True)
        video, output_audio, rendered_duration = _validate_output_info(output_info, audio_duration, style["canvas"])
        _run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(output), "-f", "null", "-"])
        digest = _sha256_file(output)
        size = output.stat().st_size
        os.replace(output, final_path)

    metadata = {
        "job_id": job_id,
        "artifact_path": str(final_path),
        "sha256": digest,
        "size_bytes": size,
        "duration_seconds": rendered_duration,
        "width": style["canvas"]["width_px"],
        "height": style["canvas"]["height_px"],
        "video_codec": _VIDEO_CODEC,
        "audio_codec": output_audio["codec_name"],
        "caption_style_id": style["preset_id"],
        "cues": [{"start_seconds": cue["start"], "end_seconds": cue["end"], "text": cue["source_text"]} for cue in cues],
        **({"listening_edit":manifest["listening_edit"],"audio_listening_verified":False} if "listening_edit" in manifest else {}),
    }
    manifest_path = job_dir / "render-manifest.json"
    manifest_tmp = job_dir / ".render-manifest.json.tmp"
    manifest_tmp.write_text(json.dumps(metadata, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.replace(manifest_tmp, manifest_path)
    metadata["manifest_path"] = str(manifest_path)
    return metadata
