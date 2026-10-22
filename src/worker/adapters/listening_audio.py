"""Local-only silence insertion and licensed voice-priority music, no paid calls."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile
from typing import TypedDict

from .listening_timeline import Timeline


class Edit(TypedDict):
    timeline: Timeline
    music_id: str
    registry_sha256: str


class ListeningAudioError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare(source: Path, job_dir: Path, edit: Edit) -> Path:
    """Derive a new WAV; original provider audio and timestamps remain unchanged."""
    timeline = edit['timeline']
    duration = timeline['source_duration_seconds']
    total = timeline['duration_seconds']
    if not all(math.isfinite(t) for t in (duration,total)) or not 0 < duration <= total <= 180:
        raise ListeningAudioError('listener_audio_duration_invalid')
    fingerprint = hashlib.sha256(json.dumps({'edit':edit,'source_sha256':sha(source)},sort_keys=True).encode()).hexdigest()
    target = job_dir/('listening-'+fingerprint+'.wav')
    registry = Path(os.environ.get('SHORTFORM_PRESENTATION_ASSETS','/opt/shortform/presentation'))
    manifest_path = registry/'manifest.json'
    if sha(manifest_path) != edit['registry_sha256']:
        raise ListeningAudioError('listener_registry_changed')
    manifest = json.loads(manifest_path.read_text())
    track = next((t for t in manifest['tracks'] if t['id']==edit['music_id']),None)
    music = None
    if edit['music_id']:
        if track is None or not track.get('commercial_youtube_sync') or Path(track['file']).name != track['file']:
            raise ListeningAudioError('listener_music_not_licensed')
        music = registry/track['file']
        if music.is_symlink() or sha(music) != track['sha256']:
            raise ListeningAudioError('listener_music_hash_mismatch')
    receipt = target.with_suffix('.json')
    if target.exists():
        if not receipt.is_file() or json.loads(receipt.read_text()).get('sha256') != sha(target):
            raise ListeningAudioError('listener_cached_audio_changed')
        return target
    graphs = []
    pieces = []
    previous = 0.0
    for i,pause in enumerate(timeline['pauses']):
        at,hold = pause['at_seconds'],pause['duration_seconds']
        if not math.isfinite(at) or not math.isfinite(hold) or not previous < at <= duration or not 0 < hold <= 1.2:
            raise ListeningAudioError('listener_audio_pause_invalid')
        graphs.append(f'[0:a]atrim=start={previous:.9f}:end={at:.9f},asetpts=PTS-STARTPTS[a{i}]')
        graphs.append(f'anullsrc=r=44100:cl=mono,atrim=duration={hold:.9f}[s{i}]')
        pieces.extend([f'[a{i}]',f'[s{i}]'])
        previous = at
    if previous < duration:
        graphs.append(f'[0:a]atrim=start={previous:.9f}:end={duration:.9f},asetpts=PTS-STARTPTS[tail]')
        pieces.append('[tail]')
    if not pieces:
        raise ListeningAudioError('listener_audio_empty')
    graphs.append(''.join(pieces)+f'concat=n={len(pieces)}:v=0:a=1,apad,atrim=duration={total:.9f}[voice]')
    inputs = ['-i',str(source)]
    output = '[voice]'
    if music is not None:
        inputs += ['-stream_loop','-1','-i',str(music)]
        levels = {'playful':0.8,'curious':0.6,'warm':0.55,'quiet':0.0}
        terms = []
        for w in timeline['windows']:
            a,b = w['start_seconds'],w['end_seconds']
            if w['music_mood'] not in levels or not 0 <= a < b <= total+.001:
                raise ListeningAudioError('listener_music_window_invalid')
            terms.append(f'if(between(t,{a:.9f},{b:.9f}),{levels[w["music_mood"]]},0)')
        envelope = '+'.join(terms) or '0'
        graphs += [f"[1:a]atrim=duration={total:.9f},asetpts=PTS-STARTPTS,volume='{envelope}':eval=frame,afade=t=in:d=0.3,afade=t=out:st={max(0,total-.8):.9f}:d=0.8[music]",
                   '[voice]asplit=2[spoken][side]',
                   '[music][side]sidechaincompress=threshold=0.015:ratio=8:attack=20:release=350[ducked]',
                   '[spoken][ducked]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.85:level=false:latency=true[mixed]']
        output = '[mixed]'
    job_dir.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.listening-',dir=job_dir) as d:
        temporary = Path(d)/'mix.wav'
        args = ['ffmpeg','-hide_banner','-loglevel','error','-y','-filter_complex_threads','1',*inputs,
                '-filter_complex',';'.join(graphs),'-map',output,'-ac','1','-ar','44100','-c:a','pcm_s16le',str(temporary)]
        subprocess.run(args,check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=120)
        os.replace(temporary,target)
        target.chmod(0o600)
        receipt.write_text(json.dumps({'sha256':sha(target),'fingerprint':fingerprint}))
        receipt.chmod(0o600)
    return target
