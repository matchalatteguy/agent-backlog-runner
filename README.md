# Agent Backlog Runner

[![CI](https://github.com/matchalatteguy/agent-backlog-runner/actions/workflows/ci.yml/badge.svg)](https://github.com/matchalatteguy/agent-backlog-runner/actions/workflows/ci.yml)

Keep a small local task queue replenished from a validated JSON or YAML template
catalog. Preview the next batch, store tasks and events in SQLite, then run short
worker commands and inspect what completed or failed.

**This project is a queue replenisher with a synchronous dispatcher.** Its sibling
[Local Agent Task Runtime](https://github.com/matchalatteguy/local-agent-task-runtime)
manages registered tasks in detached sessions and contained workspaces. Choose
this runner for recurring template work and short commands; choose the runtime
when independent workers need to outlive the dispatching process.

```text
template catalog -> depth plan -> SQLite queue -> local command -> status + events
```

## Run a real example

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/). From a fresh checkout:

```bash
uv sync --locked --extra dev
uv run python -m agent_backlog_runner.demo --output .agent-backlog/demo
```

The demo creates three Markdown documents, previews a queue replenishment plan,
enqueues three audits, then executes a real Python worker for each document.
Workers count headings and TODO lines, write JSON reports, and send heartbeats.
The final report is read after reopening SQLite:

```json
{
  "completed_tasks": 3,
  "created_tasks": 3,
  "follow_up_items": 3,
  "reports": [
    {"document": "onboarding.md", "headings": 1, "todo_items": 1},
    {"document": "release.md", "headings": 2, "todo_items": 0},
    {"document": "troubleshooting.md", "headings": 1, "todo_items": 2}
  ]
}
```

Whitespace in the printed JSON differs. Outputs are under
`.agent-backlog/demo/reports/`; task state is in
`.agent-backlog/demo/tasks.sqlite3`. Use a new `--output` directory for another
run; the demo refuses to overwrite an existing directory.

```bash
uv run agent-backlog status --db .agent-backlog/demo/tasks.sqlite3
uv run agent-backlog events --db .agent-backlog/demo/tasks.sqlite3 --limit 20
```

## Plan and replenish your own queue

A template describes a repeatable unit of work:

```yaml
templates:
  - slug: docs-refresh
    title: "Review documentation ${sequence}"
    body: "Review one documentation page and record an improvement."
    lane: docs
    role: writer
    tags: [docs]
```

Try the bundled catalog in a fresh database:

```bash
uv run agent-backlog templates validate --templates examples/templates/basic-backlog.yaml
uv run agent-backlog scheduler plan --db .agent-backlog/queue.sqlite3 \
  --templates examples/templates/basic-backlog.yaml --min-depth 2 --target-depth 4 --max-enqueue 3 --json
uv run agent-backlog scheduler run --db .agent-backlog/queue.sqlite3 \
  --templates examples/templates/basic-backlog.yaml --min-depth 2 --target-depth 4 --max-enqueue 3
```

Expected: `valid templates: 3`, a preview containing three planned tasks, then
`created 3`. Active depth counts `todo` and `running`. A later replenishment run
adds nothing while depth remains at or above the minimum. Titles are checked for
duplicates, including duplicates within the same plan.

Planning creates no tasks or events and does not advance the cursor. The CLI may
initialize the selected SQLite database on first use. Applying a plan commits
its tasks, events, and cursor together; stale plans must be regenerated.

## Dispatch short commands

Preview selected task ids with the default backend:

```bash
uv run agent-backlog dispatch --db .agent-backlog/queue.sqlite3 --max-workers 2
```

This records `dispatch_preview` events and leaves tasks `todo`. Execute the
bundled worker explicitly:

```bash
uv run agent-backlog dispatch --db .agent-backlog/queue.sqlite3 \
  --backend subprocess --max-workers 1 --timeout 60 \
  --command "bash examples/worker_echo.sh {task_id}"
```

A zero exit code marks the task `done`. Nonzero exits, timeouts, missing binaries,
and invalid workdirs mark it `failed`; later tasks in the selected batch still
run. `--max-workers` bounds the batch against current running work. Commands run
one at a time inside the CLI process, even when the limit is larger than one.

Each task can have its own `--command` and `--workdir` through `enqueue`. A
command supplied to `dispatch` overrides those commands. Supported placeholders
are `{task_id}`, `{title}`, `{lane}`, `{role}`, `{priority}`, and `{workdir}`.
Shell quoting is parsed **before** substitution, preserving each placeholder as
one argument. No shell is started automatically. Treat command templates as
trusted code, especially if you invoke a shell or interpolate into program text.

Atomic claims prevent two local dispatchers from executing the same queued task.
Completion updates preserve an operator's intervening cancellation or status
change; changing a status does not itself stop an already running process.

## Python API

```python
from agent_backlog_runner import BacklogPolicy, init_store, load_template_catalog
from agent_backlog_runner.scheduler import apply_backlog_plan, plan_backlog

with init_store(".agent-backlog/api.sqlite3") as store:
    catalog = load_template_catalog("examples/templates/basic-backlog.yaml")
    policy = BacklogPolicy(min_queue_depth=2, target_queue_depth=4)
    plan = plan_backlog(store, catalog, policy, dry_run=False)
    tasks = apply_backlog_plan(store, plan)
    print(len(tasks))
```

A plan marked `dry_run=True` cannot create tasks. See [CHANGELOG.md](CHANGELOG.md)
for the 0.2 API change. Every SQLite CLI command accepts `--db`; the fallback is
`AGENT_BACKLOG_DB`, then `.agent-backlog/tasks.sqlite3`.

## Verification and boundaries

```bash
uv run pytest
uv run ruff check .
uv build
```

CI tests Python 3.11–3.14 and executes the demo from an installed wheel outside
the checkout. Regression tests cover timeout recovery, competing claims, partial
plan rollback, duplicate titles, and stale plans.

This is an alpha library for one local machine. It has no detached worker pool,
automatic retries, process sandbox, distributed leases, or exactly-once execution.
A killed dispatcher can leave `running` tasks requiring operator recovery.
`stale` reports those tasks; it does not restart them. Timeouts terminate the
direct worker process, not an arbitrary tree of child processes. The cursor is
shared across catalogs and filters; title-based deduplication is deliberately
simple. Events retain the last 500 characters of worker output, so protect the
SQLite database and keep command output free of secrets.

[Template schema](docs/template-schema.md) · [Scheduler policy](docs/scheduler-policy.md) ·
[Dispatch](docs/dispatch-backends.md) · [Python API](docs/python-api.md) ·
[Troubleshooting](docs/troubleshooting.md)
