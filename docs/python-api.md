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

`init_store(path)` creates parent directories when needed, opens SQLite, initializes the schema idempotently, writes `schema_version=1`, and returns a `TaskStore`. Use it as a context manager or call `close()` when the script is done.

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

The built-in subprocess backend runs synchronously inside the calling process. It is suitable for short local workers and demos, not for a durable distributed worker fleet.

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
