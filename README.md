# Agent Backlog Runner

Agent Backlog Runner is a small local control plane for agent-friendly task queues. It keeps task state in SQLite, replenishes a backlog from validated templates, previews what would be created, dispatches a bounded number of local worker commands, and reports ready, running, blocked, stale, done, and failed work.

It is intentionally boring: local files, no hosted service, no credentials, and no network integration. The reusable abstraction is a tiny inspectable loop:

```text
Template catalog -> planner -> SQLite task store -> dispatcher -> local worker command
                                  |                 |
                                  |                 +-> events and task status
                                  +-> status, stale checks, and API access
```

## Who this is for

Use Agent Backlog Runner when you want a durable local queue without standing up a service:

- A solo developer running a small set of LLM or maintenance tasks and wanting a queue that survives shell restarts.
- A local automation script that should keep `N` ready tasks available from a safe template catalog.
- A teaching/demo project that needs visible task lifecycle state in SQLite.
- A lightweight agent harness where workers are ordinary local commands and the task id is the handoff token.

Do not use it when you need distributed leases, cloud scheduling, multi-host workers, secrets management, a web UI, or strict production workflow guarantees. For those, use a real workflow engine, CI system, or queue service.

## What it does

- Stores tasks and event history in SQLite.
- Loads JSON or YAML task templates with strict validation.
- Plans queue-depth replenishment with `min_queue_depth`, `target_queue_depth`, and `max_enqueue_per_cycle`.
- Supports dry-run planning with no task creation or subprocesses.
- Enqueues one-off tasks with lane, role, tags, priority, optional worker command, and optional workdir metadata.
- Caps local dispatch starts based on currently `running` tasks.
- Runs the built-in subprocess backend synchronously, one selected task at a time, inside the CLI process.
- Records heartbeats through the CLI or Python API and reports stale running tasks.
- Provides both a CLI and a small typed Python API.
- Exposes lifecycle commands: `list`, `show`, `mark`, and `heartbeat`.
- Records a schema version row so future releases have an explicit migration boundary.

## Install for development

Requirements: Python 3.11+ and `uv`.

```bash
uv sync --extra dev
uv run agent-backlog --help
```

This repository is currently an alpha-quality local-first package. Install it in a project environment rather than as a global system tool while evaluating it.

## Database configuration

Every SQLite-backed CLI command accepts `--db PATH`.

Resolution order:

1. `--db PATH`, when supplied.
2. `AGENT_BACKLOG_DB`, when set.
3. `.agent-backlog/tasks.sqlite3` in the current working directory.

Examples:

```bash
# One-off explicit database.
uv run agent-backlog init --db /tmp/agent-backlog-demo.sqlite3

# Shared default for several commands in one shell.
export AGENT_BACKLOG_DB="$PWD/.agent-backlog/tasks.sqlite3"
uv run agent-backlog init
uv run agent-backlog status
```

A `.env.example` is intentionally not included because the only environment variable is `AGENT_BACKLOG_DB` and the CLI flag is usually clearer for examples.

## First 5 minutes

Run the full local demo from a fresh checkout. The demo creates only ignored files under `.agent-backlog/`.

```bash
# 1. Create a local SQLite task store.
uv run agent-backlog init --db .agent-backlog/tasks.sqlite3

# 2. Validate the synthetic template catalog.
uv run agent-backlog templates validate --templates examples/templates/basic-backlog.yaml

# 3. Preview replenishment. This creates no tasks.
uv run agent-backlog scheduler plan \
  --db .agent-backlog/tasks.sqlite3 \
  --templates examples/templates/basic-backlog.yaml \
  --min-depth 2 \
  --target-depth 4 \
  --max-enqueue 3 \
  --dry-run \
  --json

# 4. Apply the plan and create ready tasks.
uv run agent-backlog scheduler run \
  --db .agent-backlog/tasks.sqlite3 \
  --templates examples/templates/basic-backlog.yaml \
  --min-depth 2 \
  --target-depth 4 \
  --max-enqueue 3

# 5. Inspect the queue.
uv run agent-backlog status --db .agent-backlog/tasks.sqlite3
uv run agent-backlog list --db .agent-backlog/tasks.sqlite3
uv run agent-backlog events --db .agent-backlog/tasks.sqlite3 --limit 10
```

Expected shape:

```text
initialized .agent-backlog/tasks.sqlite3
valid templates: 3
created 3
status    count
-------   -----
todo      3
```

The exact task ids and event ids are generated at runtime.

## Dispatch a harmless worker

Dispatch is opt-in. Start with the default dry-run backend; it records preview events and does not run a subprocess.

```bash
uv run agent-backlog dispatch --db .agent-backlog/tasks.sqlite3 --max-workers 2
```

Then run the example worker command. It prints the task id and exits successfully.

```bash
uv run agent-backlog dispatch \
  --db .agent-backlog/tasks.sqlite3 \
  --max-workers 1 \
  --backend subprocess \
  --command "bash examples/worker_echo.sh {task_id}"

uv run agent-backlog status --db .agent-backlog/tasks.sqlite3
uv run agent-backlog events --db .agent-backlog/tasks.sqlite3 --limit 20
```

Worker commands can be supplied globally with `dispatch --command` or stored per task with `enqueue --command`. Per-task commands are useful when a backlog mixes roles or tools:

```bash
uv run agent-backlog enqueue \
  --db .agent-backlog/tasks.sqlite3 \
  --title "Run docs worker" \
  --body "Process one local docs task." \
  --lane docs \
  --role writer \
  --tag docs \
  --workdir "$PWD" \
  --command "bash examples/worker_echo.sh {task_id}"

uv run agent-backlog dispatch \
  --db .agent-backlog/tasks.sqlite3 \
  --backend subprocess \
  --max-workers 1
```

Supported command placeholders are `{task_id}`, `{title}`, `{lane}`, `{role}`, `{priority}`, and `{workdir}`. The template is split with shell-style quoting before placeholder values are substituted, so task metadata cannot create extra subprocess argv tokens. Unknown placeholders fail closed before a subprocess starts.

## Template example

```yaml
templates:
  - slug: docs-refresh
    title: "Refresh docs section ${sequence}"
    body: "Review one documentation page and write a short improvement note."
    role: writer
    lane: docs
    priority: 20
    tags: [docs, quality]
    acceptance:
      - "The note names the page and the suggested edit."
```

Use `--lane docs` or `--tag quality` on scheduler commands to narrow which templates are eligible.

## CLI map

```text
agent-backlog init [--db PATH]
agent-backlog templates validate --templates PATH
agent-backlog scheduler plan [--db PATH] --templates PATH [--dry-run] [--json]
agent-backlog scheduler run [--db PATH] --templates PATH [--dry-run]
agent-backlog enqueue [--db PATH] --title TEXT [--body TEXT | --body-file PATH] [--lane SLUG] [--role SLUG] [--tag SLUG] [--priority N] [--command CMD] [--workdir PATH]
agent-backlog list [--db PATH] [--status STATUS] [--format table|json]
agent-backlog show [--db PATH] TASK_ID [--format table|json]
agent-backlog mark [--db PATH] TASK_ID --status todo|running|blocked|done|failed|cancelled [--message TEXT]
agent-backlog heartbeat [--db PATH] TASK_ID [--payload JSON_OBJECT]
agent-backlog dispatch [--db PATH] [--backend dry-run|subprocess] [--max-workers N] [--command CMD] [--timeout SECONDS]
agent-backlog status [--db PATH] [--format table|json]
agent-backlog events [--db PATH] [--limit N] [--format table|json]
agent-backlog stale [--db PATH] [--after 30m] [--format table|json]
```

The CLI intentionally keeps retry/cancel as status transitions: use `mark TASK_ID --status todo` to requeue and `mark TASK_ID --status cancelled` to cancel.

## Python API sketch

```python
from agent_backlog_runner import BacklogPolicy, TaskStatus, init_store, load_template_catalog
from agent_backlog_runner.scheduler import apply_backlog_plan, plan_backlog

with init_store(".agent-backlog/tasks.sqlite3") as store:
    catalog = load_template_catalog("examples/templates/basic-backlog.yaml")
    plan = plan_backlog(store, catalog, BacklogPolicy(min_queue_depth=2, target_queue_depth=4))
    created = apply_backlog_plan(store, plan)

    for task in created:
        store.record_heartbeat(task.id, {"note": "worker started"})
        store.mark_task(task.id, TaskStatus.DONE, "demo complete")

    print([task.id for task in created])
```

See `docs/python-api.md` for a fuller API walkthrough.

## Docs

- `docs/first-5-minutes.md`: copy/paste walkthrough with restart notes.
- `docs/template-schema.md`: YAML/JSON template fields and validation rules.
- `docs/scheduler-policy.md`: queue-depth planning and dry-run behavior.
- `docs/dispatch-backends.md`: dry-run and subprocess dispatch boundaries.
- `docs/status-and-heartbeats.md`: status counts, events, stale checks, and heartbeat API.
- `docs/python-api.md`: common library calls and lifecycle examples.
- `docs/troubleshooting.md`: common setup, template, database, dispatch, and stale-task issues.
- `docs/safety-model.md`: local-first side-effect boundaries.

## Troubleshooting

- `error: database path is a directory`: pass a SQLite file path, not a folder.
- `unsupported command placeholder`: worker commands only support `{task_id}`, `{title}`, `{lane}`, `{role}`, `{priority}`, and `{workdir}`.
- `subprocess backend requires a policy command or per-task command`: pass `dispatch --command` or enqueue tasks with `--command`.
- Stale tasks are reports only; use `mark TASK_ID --status failed|todo|blocked` after you decide what happened.
- The SQLite schema stores `schema_version=1`; future incompatible changes should ship migrations instead of silently changing tables.

## Safety model

Agent Backlog Runner is local-first and fail-closed. It rejects unsafe identifiers, validates template fields, performs dry runs without writes, and never removes directories as part of normal operation. Worker dispatch is disabled unless explicitly requested.

Subprocess dispatch still executes local binaries on your machine. Treat command templates as code, keep templates trusted, use timeouts, and prefer fixed commands that receive only `{task_id}` over commands that interpolate arbitrary task text.

## Limitations and alpha notes

- The subprocess backend is synchronous. It is bounded, but it is not a durable worker pool.
- The SQLite schema is created automatically and records `schema_version=1`; future releases still need real migrations before incompatible schema changes.
- Duplicate avoidance is title-based and intentionally simple.
- Scheduler cursor state is global for the store, not per catalog/filter combination.
- Events keep short command output snippets, not full logs or artifacts.
- CI is provided for GitHub Actions, but this repository has not been published or wired to a public remote by this task.

## Non-goals

This project is not Celery, Airflow, GitHub Actions, a hosted project-management app, a distributed workflow engine, a secrets manager, or a replacement for a full orchestration platform. It is useful when you want a tiny local queue with inspectable SQLite state and worker commands you control.
