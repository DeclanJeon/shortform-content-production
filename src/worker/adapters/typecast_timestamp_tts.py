"""Typecast Timestamp TTS adapter; provider calls require explicit paid-call approval."""

from __future__ import annotations

import base64
import json
import io
import math
import wave
import urllib.request
from typing import Any, Callable

API_URL = "https://api.typecast.ai/v1/text-to-speech/with-timestamps"
MAX_TEXT_LENGTH = 2000
MAX_AUDIO_SECONDS = 180.0
# ssfm-v30 accepts these preset names; intensity and tempo bounds are the provider's documented ranges.
EMOTION_PRESETS = ("normal", "happy", "sad", "angry", "whisper", "toneup", "tonedown")
EMOTION_INTENSITY_RANGE = (0.0, 2.0)
TEMPO_RANGE = (0.5, 2.0)


def _finite_seconds(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be numeric seconds")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric seconds") from exc
    if not math.isfinite(result):
        raise ValueError(f"{field} must be finite")
    return result


def make_caption_cues(words: Any, duration_seconds: float, *, max_chars: int = 18, max_seconds: float = 3.0) -> list[dict[str, Any]]:
    """Group Typecast word timestamps into readable, ordered, non-overlapping SRT cues."""
    if not isinstance(words, list) or not words:
        raise ValueError("Typecast response must contain word timestamps")
    cues: list[dict[str, Any]] = []
    group: list[dict[str, Any]] = []
    prior_start = -1.0
    prior_end = 0.0

    def flush() -> None:
        if not group:
            return
        start = group[0]["start"]
        end = group[-1]["end"]
        if cues and start < cues[-1]["end_seconds"]:
            start = cues[-1]["end_seconds"]
        if start >= end:
            raise ValueError("caption group has no non-overlapping duration")
        cues.append({
            "start_seconds": start,
            "end_seconds": end,
            "text": " ".join(word["text"] for word in group).strip(),
        })
        group.clear()
    for index, word in enumerate(words, start=1):
        if not isinstance(word, dict) or not isinstance(word.get("text"), str) or not word["text"].strip():
            raise ValueError(f"word timestamp {index} is malformed")
        start = _finite_seconds(word.get("start"), f"word {index} start")
        end = _finite_seconds(word.get("end"), f"word {index} end")
        text = word["text"]
        if start < prior_start or start < 0 or end <= start or end > duration_seconds + 0.05 or start < prior_end - 0.05:
            raise ValueError(f"word timestamp {index} is out of order or outside the audio duration")
        prior_start, prior_end = start, end
        candidate_chars = sum(len(item["text"]) for item in group) + len(group) + len(text)
        candidate_duration = end - (group[0]["start"] if group else start)
        if group and (candidate_chars > max_chars or candidate_duration > max_seconds):
            flush()
        group.append({"text": text, "start": start, "end": end})
        if text.rstrip().endswith((".", "!", "?", "。", "！", "？")):
            flush()
    flush()
    return cues


def parse_timestamp_response(response: Any) -> dict[str, Any]:
    """Validate provider JSON and decode its base64 audio without writing files."""
    if not isinstance(response, dict):
        raise ValueError("Typecast response must be a JSON object")
    duration = _finite_seconds(response.get("audio_duration"), "audio_duration")
    if not 0 < duration <= MAX_AUDIO_SECONDS:
        raise ValueError("Typecast audio duration must be within 0-180 seconds")
    audio_format = response.get("audio_format")
    if audio_format not in {"wav", "mp3"}:
        raise ValueError("Typecast returned an unsupported audio format")
    encoded = response.get("audio")
    if not isinstance(encoded, str) or not encoded:
        raise ValueError("Typecast response is missing base64 audio")
    try:
        audio = base64.b64decode(encoded, validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise ValueError("Typecast response audio is not valid base64") from exc
    if not audio:
        raise ValueError("Typecast response audio is empty")
    captions = make_caption_cues(response.get("words"), duration)
    return {"audio_bytes": audio, "audio_format": audio_format, "duration_seconds": duration, "captions": captions, "word_timestamps": make_caption_cues(response.get("words"), duration, max_chars=1)}


def synthesize_timestamped(
    text: str,
    voice_id: str,
    api_key: str,
    *,
    allow_paid_generation: bool,
    emotion_preset: str = "normal",
    emotion_intensity: float = 1.0,
    tempo: float = 1.0,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> dict[str, Any]:
    """Call Typecast only when explicitly enabled; never log or persist the key."""
    if allow_paid_generation is not True:
        raise PermissionError("Typecast generation requires allow_paid_generation=true")
    if not isinstance(api_key, str) or not api_key.strip():
        raise ValueError("TYPECAST_API_KEY is required")
    if not isinstance(voice_id, str) or not voice_id.strip():
        raise ValueError("Typecast voice_id is required")
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT_LENGTH:
        raise ValueError("text must contain 1-2000 characters")
    if emotion_preset not in EMOTION_PRESETS:
        raise ValueError(f"emotion_preset must be one of {sorted(EMOTION_PRESETS)}")
    if not EMOTION_INTENSITY_RANGE[0] <= float(emotion_intensity) <= EMOTION_INTENSITY_RANGE[1]:
        raise ValueError(f"emotion_intensity must be within {EMOTION_INTENSITY_RANGE}")
    if not TEMPO_RANGE[0] <= float(tempo) <= TEMPO_RANGE[1]:
        raise ValueError(f"tempo must be within {TEMPO_RANGE}")

    payload = {
        "voice_id": voice_id,
        "text": text,
        "model": "ssfm-v30",
        "language": "kor",
        "prompt": {
            "emotion_type": "preset",
            "emotion_preset": emotion_preset,
            "emotion_intensity": float(emotion_intensity),
        },
        "output": {"audio_format": "wav", "audio_tempo": float(tempo), "target_lufs": -14},
    }
    request = urllib.request.Request(
        API_URL + "?granularity=word",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"X-API-KEY": api_key.strip(), "Content-Type": "application/json"},
        method="POST",
    )
    with opener(request, timeout=60) as response:
        status = getattr(response, "status", 200)
        if status != 200:
            raise RuntimeError(f"Typecast request failed with HTTP {status}")
        body = response.read()
    try:
        decoded = json.loads(body)
    except (TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Typecast returned invalid JSON") from exc
    return parse_timestamp_response(decoded)


def synthesize_scenes_timestamped(scenes, voice_id, api_key, *, allow_paid_generation, synthesize_scene=None):
    """Synthesize each storyboard narration and join one timestamped WAV timeline."""
    if allow_paid_generation is not True:
        raise PermissionError("Typecast generation requires allow_paid_generation=true")
    if not isinstance(scenes, list) or not 1 <= len(scenes) <= 20:
        raise ValueError("scenes must contain between 1 and 20 entries")
    narrations = []
    for index, scene in enumerate(scenes, start=1):
        if not isinstance(scene, dict) or scene.get("scene_no") != index:
            raise ValueError("scene_no values must be consecutive starting at 1")
        text = scene.get("narration")
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT_LENGTH:
            raise ValueError(f"scene {index} narration must contain 1-2000 characters")
        narrations.append(text)
    synthesize = synthesize_scene or synthesize_timestamped
    pcm_chunks, captions, timings = [], [], []
    params_ref, cursor = None, 0.0
    for index, (scene, text) in enumerate(zip(scenes, narrations), start=1):
        overrides = {key: scene[key] for key in ("emotion_preset", "emotion_intensity", "tempo") if key in scene}
        result = synthesize(text, voice_id, api_key, allow_paid_generation=True, **overrides)
        if not isinstance(result, dict) or result.get("audio_format") != "wav":
            raise ValueError("scene narration must return WAV audio")
        data = result.get("audio_bytes")
        if not isinstance(data, (bytes, bytearray)) or not data:
            raise ValueError(f"scene {index} narration has no audio bytes")
        try:
            with wave.open(io.BytesIO(data), "rb") as wav:
                params = wav.getparams()
                frame_count = wav.getnframes()
                frames = wav.readframes(frame_count)
        except (wave.Error, EOFError) as exc:
            raise ValueError(f"scene {index} narration is not a valid WAV") from exc
        if params_ref is None:
            params_ref = params
        elif (params.nchannels, params.sampwidth, params.framerate, params.comptype) != (params_ref.nchannels, params_ref.sampwidth, params_ref.framerate, params_ref.comptype):
            raise ValueError("Typecast scene WAV formats are incompatible")
        duration = frame_count / float(params.framerate)
        if duration <= 0 or cursor + duration > MAX_AUDIO_SECONDS:
            raise ValueError("combined Typecast scene narration must be at most 180 seconds")
        cue_list = result.get("captions")
        if not isinstance(cue_list, list) or not cue_list:
            raise ValueError(f"scene {index} narration returned no caption cues")
        for cue in cue_list:
            if not isinstance(cue, dict):
                raise ValueError(f"scene {index} caption cue is malformed")
            start = _finite_seconds(cue.get("start_seconds"), f"scene {index} caption start")
            end = _finite_seconds(cue.get("end_seconds"), f"scene {index} caption end")
            cue_text = cue.get("text")
            if not isinstance(cue_text, str) or not cue_text.strip() or start < 0 or end <= start or end > duration + 0.02:
                raise ValueError(f"scene {index} caption cue is outside its narration")
            captions.append({"start_seconds": cursor + start, "end_seconds": cursor + end, "text": cue_text.strip()})
        timings.append({"scene_no": index, "start_seconds": cursor, "end_seconds": cursor + duration})
        pcm_chunks.append(frames)
        cursor += duration
    output = io.BytesIO()
    with wave.open(output, "wb") as joined:
        joined.setparams(params_ref)
        joined.writeframes(b"".join(pcm_chunks))
    return {"audio_bytes": output.getvalue(), "audio_format": "wav", "duration_seconds": cursor, "scene_timings": timings, "captions": captions}
