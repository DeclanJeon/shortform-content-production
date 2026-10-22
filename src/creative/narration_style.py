"""Negative register filter, not a substitute for semantic spoken-story review."""
from __future__ import annotations

import re
from typing import TypedDict


class RegisterMetrics(TypedDict):
    utterances: int
    report_endings: int
    longest_report_run: int


def register_metrics(text: str) -> RegisterMetrics:
    """Ignore quoted character speech; inspect narrator sentence endings only."""
    narrator=re.sub(r'“[^”]*”|‘[^’]*’|"[^"\n]*"', '', text)
    sentences=[re.sub(r'[\s.!?。！？…]+$','',s) for s in re.split(r'(?<=[.!?。！？])\s*|\n+',narrator) if s.strip()]
    run=longest=report=0
    for sentence in sentences:
        formal=sentence.endswith('다')
        report+=int(formal);run=run+1 if formal else 0;longest=max(longest,run)
    return {'utterances':len(sentences),'report_endings':report,'longest_report_run':longest}


def report_register(metrics: RegisterMetrics) -> bool:
    return metrics['longest_report_run']>=3 or (metrics['utterances']>=6 and metrics['report_endings']/metrics['utterances']>=.6)
