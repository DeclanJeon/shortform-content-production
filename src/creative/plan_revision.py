"""Version identities for pre-media plan revisions; old handoffs stay immutable."""
from __future__ import annotations

from .planning_contract import JsonObject, PlanningFailure


def plan_version(payload: JsonObject) -> int:
    value=payload.get('plan_version',1)
    if type(value) is not int or not 1<=value<=20:
        raise PlanningFailure('planning_recovery_conflict','invalid plan version')
    return value


def handoff_key(task_id: int, payload: JsonObject) -> str:
    version=plan_version(payload)
    return f'creative-plan-ready:{task_id}'+(f':v{version}' if version>1 else '')


def story_version(payload: JsonObject, *, media: bool = False) -> int:
    return 2*plan_version(payload)-(0 if media else 1)
