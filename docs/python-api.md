# Python API

The CLI is the fastest way to try Agent Backlog Runner, but the package is designed to be embedded in local scripts. The API is small and maps directly to the internal loop:

```text
load templates -> plan backlog -> create tasks -> inspect/dispatch/mark tasks
```

## Open a store

```python
from agent_backlog_runner import init_store

with init_store(".agent-backlog/tasks.sqlite3") as store:
    print(store.path)
    print(store.schema_version())
```

`init_store(path)` creates parent directories when needed, opens SQLite, initializes/migrates to schema 2, and returns a `TaskStore`. Use it as a context manager or call `close()` when done. Schema-1 tasks/events remain intact; old running tasks need explicit inspection/recovery because their process identities are unknown.

## Create one task

```python
from agent_backlog_runner import init_store

store = init_store(".agent-backlog/tasks.sqlite3")
task = store.create_task(
    title="Review README quickstart",
    body="Check that every command still matches the current CLI.",
    lane="docs",
    role="writer",
    tags=("docs", "quality"),
    priority=20,
    command="bash examples/worker_echo.sh {task_id}",
    workdir=".",
)
print(task.id)
store.close()
```

Task ids, lanes, roles, and tags are validated as safe slugs. Task title and body are stored as data; do not put secrets in them because they may be printed in events, logs, or reports.

## Plan and apply template replenishment

```python
from agent_backlog_runner import BacklogPolicy, init_store, load_template_catalog
from agent_backlog_runner.scheduler import apply_backlog_plan, plan_backlog

store = init_store(".agent-backlog/tasks.sqlite3")
catalog = load_template_catalog("examples/templates/basic-backlog.yaml")
policy = BacklogPolicy(
    min_queue_depth=2,
    target_queue_depth=4,
    max_enqueue_per_cycle=3,
    lanes=("docs",),
)

plan = plan_backlog(store, catalog, policy, dry_run=True)
print([item.title for item in plan.planned])

real_plan = plan_backlog(store, catalog, policy, dry_run=False)
created = apply_backlog_plan(store, real_plan)
print([task.id for task in created])
store.close()
```

Planning is side-effect-free until `apply_backlog_plan()` is called.

## Inspect tasks and events

```python
from agent_backlog_runner import TaskStatus, init_store

store = init_store(".agent-backlog/tasks.sqlite3")
ready = store.list_tasks((TaskStatus.TODO,))
for task in ready:
    print(task.id, task.priority, task.lane, task.title)

for event in store.events(limit=10):
    print(event.id, event.task_id, event.event_type, event.message)
store.close()
```

Use `store.get_task(task_id)` when you already have a task id. It returns `None` when the task is absent.

## Mark task lifecycle state

```python
from agent_backlog_runner import TaskStatus, init_store

store = init_store(".agent-backlog/tasks.sqlite3")
task = store.create_task(title="Demo lifecycle", body="Mark through several states.")

store.mark_task(task.id, TaskStatus.RUNNING, "worker claimed task")
store.record_heartbeat(task.id, {"note": "halfway"})
store.mark_task(task.id, TaskStatus.DONE, "finished successfully")
store.close()
```

Available statuses are `todo`, `running`, `blocked`, `done`, `failed`, and `cancelled`. `mark_task()` records an event for each transition.

## Dispatch from Python

```python
from agent_backlog_runner import DispatchPolicy, init_store
from agent_backlog_runner.dispatch import dispatch_ready

store = init_store(".agent-backlog/tasks.sqlite3")
result = dispatch_ready(
    store,
    DispatchPolicy(
        max_concurrent_workers=1,
        backend="subprocess",
        command="bash examples/worker_echo.sh {task_id}",
        timeout_seconds=60,
    ),
)
print(result.started, result.skipped_reason)
store.close()
```

The calling process stays open, while up to `max_concurrent_workers` subprocesses actually run concurrently. Set `drain=True` to consume the initial queued snapshot and fill slots as workers finish. Every selected attempt has durable history and capped per-stream log files. Process execution requires POSIX; this is a local coordinator, not a distributed worker fleet.

## Finite batches and explicit retry

```python
from pathlib import Path
from agent_backlog_runner import (
    DispatchPolicy, dispatch_ready, enqueue_batch, init_store,
    load_batch_manifest, write_queue_report,
)

manifest = load_batch_manifest("checks.yaml", root=".")
with init_store(".agent-backlog/checks.sqlite3") as store:
    tasks = enqueue_batch(store, manifest, run_id="release-check")
    result = dispatch_ready(store, DispatchPolicy(2, "subprocess", drain=True))
    for task_id in result.failed:
        print(store.attempts(task_id))
    write_queue_report(store, md_out=Path(".agent-backlog/report.md"))
```

After repairing inputs, `store.retry_task(task_id)` schedules another bounded attempt. `store.request_cancel(task_id)` asks the active coordinator to stop its own process; it does not signal a saved PID. `recover_task(store, task_id, stale_after=1800)` refuses known live owners/workers/groups and abandons stale uncertain work without requeueing it. Read [recovery semantics](batches-and-recovery.md) before using the optional unknown-identity acknowledgement.

`create_task(command_argv=(...))` stores exact arguments without legacy string-template substitution. Optional `timeout_seconds`, `max_attempts`, and `retry_backoff_seconds` control bounded execution. The low-level `mark_task` and old `claim_task` APIs remain manual bookkeeping; prefer managed dispatch/retry/recovery for process work. Editing raw statuses does not bypass active-attempt claims or establish that side effects stopped.

## Status snapshots

```python
from agent_backlog_runner import init_store
from agent_backlog_runner.status import get_status_snapshot

store = init_store(".agent-backlog/tasks.sqlite3")
snapshot = get_status_snapshot(store, stale_after=30 * 60)
print(snapshot.counts)
print([item.task.id for item in snapshot.stale_tasks])
store.close()
```

A stale task is a `running` task whose latest heartbeat/update is older than the threshold. The library reports stale tasks; it does not automatically kill or requeue them.
