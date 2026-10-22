"""Bounded external inspiration is not an author-supplied creative document."""
import json
from collections.abc import Mapping
from typing import Final

EXTERNAL_INSPIRATION: Final = 'EXTERNAL_INSPIRATION_ONLY'


def sheet_inspiration_document(seed: Mapping[str, str]) -> str | None:
    """Create an explicit external-material packet, never a licence/author brief."""
    title = seed.get('title', '').strip()
    summary = seed.get('source_summary', '').strip()
    if len(title) < 4 or len(summary) < 10:
        return None
    return json.dumps({'material_kind': EXTERNAL_INSPIRATION, 'title': title,
        'external_summary': summary, 'scope': 'ideas_only_not_adaptation_permission',
        'evidence_scope': 'supplied_title_and_short_summary_only', 'article_body_fetched': False},
        ensure_ascii=False, sort_keys=True, separators=(',', ':'))
