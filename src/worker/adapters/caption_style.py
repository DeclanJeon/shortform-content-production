"""Pinned caption-style contract for the inactive render workflow."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any

PRESET_PATH = Path(__file__).with_name("caption_style_v10.json")


def load_default_caption_style() -> dict[str, Any]:
    return json.loads(PRESET_PATH.read_text(encoding="utf-8"))


def validate_render_caption_style(candidate: Any) -> dict[str, Any]:
    """Accept only the reviewed, bounded preset; reject caller-defined style drift."""
    expected = load_default_caption_style()
    if not isinstance(candidate, dict) or candidate != expected:
        raise ValueError("caption_style_must_match_approved_preset")
    return expected
