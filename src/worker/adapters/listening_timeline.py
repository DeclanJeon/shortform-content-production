"""Retiming from real word boundaries, never estimated words or shortened speech."""
from __future__ import annotations

from typing import TypedDict, NotRequired
import math


class Cue(TypedDict):
    text: str
    start_seconds: float
    end_seconds: float


class Listening(TypedDict):
    beat: str
    pause_after_seconds: float
    music_mood: str


class Performance(TypedDict):
    critical: bool
    listening: NotRequired[Listening]


class Shot(TypedDict):
    id: str
    performance: NotRequired[Performance]


class Segment(TypedDict):
    text: str


class Story(TypedDict):
    shots: list[Shot]
    narration_segments: list[Segment]


class Speech(TypedDict):
    duration_seconds: float
    captions: list[Cue]
    word_timestamps: NotRequired[list[Cue]]


class Pause(TypedDict):
    at_seconds: float
    duration_seconds: float


class Window(TypedDict):
    shot_id: str
    start_seconds: float
    end_seconds: float
    music_mood: str


class Timeline(TypedDict):
    word_timestamps: NotRequired[list[Cue]]
    alignment_basis: str
    source_duration_seconds: float
    duration_seconds: float
    captions: list[Cue]
    pauses: list[Pause]
    windows: list[Window]


class ListeningError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def normalized(text: str) -> str:
    return ''.join(c for c in text.casefold() if c.isalnum())


def timeline(plan: Story, speech: Speech) -> Timeline:
    """Map exact segment ends to observed cue ends; retain silence on its own shot."""
    duration = speech['duration_seconds']
    cues = speech['captions']
    if (isinstance(duration, bool) or not math.isfinite(duration) or not 0 < duration <= 180
            or not cues or len(plan['shots']) != len(plan['narration_segments'])):
        raise ListeningError('listener_timeline_invalid')
    words = speech.get('word_timestamps')
    units = words or cues
    offsets = {}
    positions = []
    count = 0
    previous = 0.0
    for cue in units:
        a, b = cue['start_seconds'], cue['end_seconds']
        if (not all(math.isfinite(t) for t in (a,b)) or a < previous or b <= a or b > duration+.001):
            raise ListeningError('listener_cue_invalid')
        count += len(normalized(cue['text']))
        offsets[count] = b
        positions.append((count,b))
        previous = b
    text = ''.join(s['text'] for s in plan['narration_segments'])
    if normalized(text) != normalized(''.join(c['text'] for c in cues)):
        raise ListeningError('listener_caption_text_mismatch')
    if normalized(text) != normalized(''.join(c['text'] for c in units)):
        raise ListeningError('listener_word_text_mismatch')
    targets = []
    running = 0
    for s in plan['narration_segments']:
        running += len(normalized(s['text']));targets.append(running)
    if words:
        ends = [offsets.get(t) for t in targets]
        if any(e is None for e in ends):raise ListeningError('listener_segment_not_at_word_boundary')
    else:
        # Older successful jobs retained grouped cues, not their original words.
        # Choose monotone REAL cue ends; never invent word times or rebuy speech.
        if len(positions)<len(targets):raise ListeningError('listener_grouped_cues_insufficient')
        states = {0:(0,[])}
        for target in targets[:-1]:
            following = {}
            for j in range(1,len(positions)):
                candidates=[(cost+(positions[j-1][0]-target)**2,path+[j])
                            for prev,(cost,path) in states.items() if prev<j]
                if candidates:following[j]=min(candidates,key=lambda x:x[0])
            states=following
        if not states:raise ListeningError('listener_grouped_alignment_unavailable')
        selected=min(states.values(),key=lambda x:x[0])[1]+[len(positions)]
        ends=[positions[j-1][1] for j in selected]
    pauses: list[Pause] = []
    windows: list[Window] = []
    offset, added, last = 0, 0.0, 0.0
    for i, (shot, segment) in enumerate(zip(plan['shots'],plan['narration_segments'])):
        offset += len(normalized(segment['text']))
        end = ends[i]
        performance = shot.get('performance',{})
        fallback_hold = .8 if i==len(plan['shots'])-1 else .6 if performance.get('critical') else 0.0
        direction = performance.get('listening',{'beat':'reaction' if fallback_hold else 'flow',
                                    'pause_after_seconds':fallback_hold,'music_mood':'quiet'})
        hold = direction['pause_after_seconds']
        if isinstance(hold,bool) or not math.isfinite(hold) or not 0 <= hold <= 1.2:
            raise ListeningError('listener_pause_invalid')
        if direction['music_mood'] not in ('playful','curious','warm','quiet'):
            raise ListeningError('listener_music_mood_invalid')
        # Frame-aligned added silence; no changes to the approved words or TTS.
        hold = round(hold*30)/30
        if hold:
            pauses.append({'at_seconds':end,'duration_seconds':hold})
        added += hold
        stop = (math.ceil(duration*30)/30 if i == len(plan['shots'])-1 else round(end*30)/30) + added
        if stop <= last:raise ListeningError('listener_shot_too_short')
        windows.append({'shot_id':shot['id'],'start_seconds':last,'end_seconds':stop,
                        'music_mood':direction['music_mood']})
        last = stop
    total = math.ceil(duration*30)/30 + added
    if total > 180:
        raise ListeningError('listener_runtime_exceeds_180')
    if words:
        # Keep readable provider groups, splitting any group at an inserted hold.
        group_ends=set();cumulative=0
        for c in cues:
            cumulative+=len(normalized(c['text']));group_ends.add(cumulative)
        chunks=[];group=[];cumulative=0;pause_ends={p['at_seconds'] for p in pauses}
        for word in units:
            group.append(word);cumulative+=len(normalized(word['text']))
            if cumulative in group_ends or word['end_seconds'] in pause_ends:
                chunks.append({'text':' '.join(w['text'] for w in group),
                               'start_seconds':group[0]['start_seconds'],'end_seconds':group[-1]['end_seconds']})
                group=[]
        if group:raise ListeningError('listener_caption_group_mismatch')
        cues=chunks
    shifted = [{**c,
        'start_seconds':c['start_seconds']+sum(p['duration_seconds'] for p in pauses if p['at_seconds'] <= c['start_seconds']),
        'end_seconds':c['end_seconds']+sum(p['duration_seconds'] for p in pauses if p['at_seconds'] < c['end_seconds'])}
        for c in cues]
    return {**({'word_timestamps':[{**c,
        'start_seconds':c['start_seconds']+sum(p['duration_seconds'] for p in pauses if p['at_seconds'] <= c['start_seconds']),
        'end_seconds':c['end_seconds']+sum(p['duration_seconds'] for p in pauses if p['at_seconds'] < c['end_seconds'])}
        for c in words]} if words else {}),
            'alignment_basis':'provider_word_timestamps' if words else 'provider_grouped_caption_boundaries',
            'source_duration_seconds':duration,'duration_seconds':total,
            'captions':shifted,'pauses':pauses,'windows':windows}
