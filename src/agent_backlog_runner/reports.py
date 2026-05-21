from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from typing import Any

from .scheduler import BacklogPlan
from .status import StatusSnapshot, format_snapshot


def _default(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, tuple):
        return list(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def plan_to_json(plan: BacklogPlan) -> str:
    return json.dumps(plan, default=_default, indent=2, sort_keys=True)


def snapshot_to_json(snapshot: StatusSnapshot) -> str:
    return json.dumps(snapshot, default=_default, indent=2, sort_keys=True)


def snapshot_to_table(snapshot: StatusSnapshot) -> str:
    return format_snapshot(snapshot)
