"""Conservative local recovery; this module never signals stored process IDs."""

from __future__ import annotations

import os
import time

from .models import TaskStatus
from .store import TaskStore


def _pid_exists(pid: int | None, *, group: bool = False) -> bool:
    if pid is None:
        return False
    try:
        if group and os.name == "posix":
            os.killpg(pid, 0)
        else:
            os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def recover_task(
    store: TaskStore, task_id: str, *, stale_after: float = 1800, acknowledge_unknown: bool = False
) -> None:
    if stale_after < 0:
        raise ValueError("stale_after must be non-negative")
    with store.transaction():
        task = store.get_task(task_id)
        if task is None:
            raise KeyError(task_id)
        active = [item for item in store.attempts(task_id) if item["finished_at"] is None]
        if task.status != TaskStatus.RUNNING and not active:
            raise ValueError("task has no unfinished running work")
        basis = task.heartbeat_at or task.updated_at
        if time.time() - basis < stale_after:
            raise ValueError("task is not stale; wait or inspect with stale --after")
        for attempt in active:
            if _pid_exists(attempt["owner_pid"]):
                raise ValueError(
                    "dispatcher PID still exists; cancel through its coordinator or inspect it"
                )
            if _pid_exists(attempt["worker_pid"]) or _pid_exists(
                attempt["process_group"], group=True
            ):
                raise ValueError(
                    "worker PID or process group still exists; stop and inspect it before recovery"
                )
            if attempt["worker_pid"] is None and not acknowledge_unknown:
                raise ValueError(
                    "worker identity was not recorded; inspect possible side effects "
                    "and pass --acknowledge-unknown"
                )
        if not active and not acknowledge_unknown:
            raise ValueError(
                "legacy running task has no attempt identity; inspect it "
                "and pass --acknowledge-unknown"
            )
        for attempt in active:
            store.finish_attempt(
                attempt["claim_token"],
                "abandoned",
                "stale attempt explicitly recovered; inspect outputs before retry",
            )
        if not active:
            store.mark_task(
                task_id, TaskStatus.FAILED, "untracked running work explicitly abandoned"
            )
        store.add_event(task_id, "recovered", "recovered; retry is a separate explicit action", {})
