from __future__ import annotations

import json
import os
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from .scheduler import BacklogPlan
from .status import StatusSnapshot, format_snapshot, get_status_snapshot
from .store import TaskStore


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


def write_queue_report(
    store: TaskStore, *, json_out: Path | None = None, md_out: Path | None = None
) -> None:
    if not json_out and not md_out:
        raise ValueError("choose --json-out or --md-out")
    tasks, attempts = store.list_tasks(), store.attempts()
    snapshot = get_status_snapshot(store)
    if json_out:
        json_out.parent.mkdir(parents=True, exist_ok=True)
        json_out.write_text(
            json.dumps(
                {
                    "summary": asdict(snapshot),
                    "tasks": [asdict(task) for task in tasks],
                    "attempts": attempts,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    if md_out:
        md_out.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            "# Local command queue report",
            "",
            "```text",
            format_snapshot(snapshot),
            "```",
            "",
            "| Task | Status | Attempts | Latest outcome | Exit | Logs |",
            "|---|---|---:|---|---|---|",
        ]
        for task in tasks:
            latest = next((attempt for attempt in attempts if attempt["task_id"] == task.id), None)
            title = task.title.replace("|", "\\|").replace("\n", " ")
            logs = []
            if latest:
                for stream in ("stdout", "stderr"):
                    if latest[stream + "_path"]:
                        path = os.path.relpath(latest[stream + "_path"], md_out.resolve().parent)
                        logs.append(f"[{stream}](<{path}>)")
            outcome, code = (
                (latest["outcome"], latest["exit_code"]) if latest else ("not started", "")
            )
            lines.append(
                f"| {title} (`{task.id}`) | {task.status.value} | "
                f"{task.attempt_count}/{task.max_attempts} | {outcome} | {code} | "
                f"{' · '.join(logs)} |"
            )
        lines += [
            "",
            "A zero command exit is completion, not a correctness guarantee. "
            "Inspect retained logs and each command's reports.",
            "",
        ]
        md_out.write_text("\n".join(lines), encoding="utf-8")
