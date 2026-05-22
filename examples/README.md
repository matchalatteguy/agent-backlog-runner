# Examples

The examples are synthetic and safe to run locally. They show queue mechanics, not domain-specific automation. Use them as starting points for your own local maintenance, documentation, or agent-demo queues.

## Files

- `templates/basic-backlog.yaml`: three task templates for documentation refresh, test hardening, and dependency notes.
- `worker_echo.sh`: harmless local demo worker. It prints the task id and exits successfully.

## Scenario 1: documentation maintenance queue

Use the checked-in catalog as a small docs-maintenance backlog:

```bash
uv run agent-backlog init --db .agent-backlog/docs.sqlite3
uv run agent-backlog templates validate --templates examples/templates/basic-backlog.yaml
uv run agent-backlog scheduler run \
  --db .agent-backlog/docs.sqlite3 \
  --templates examples/templates/basic-backlog.yaml \
  --lane docs \
  --min-depth 1 \
  --target-depth 2 \
  --max-enqueue 2
uv run agent-backlog status --db .agent-backlog/docs.sqlite3
```

This creates ready documentation tasks only. A human or external worker can inspect task ids from status/events and process them.

## Scenario 2: harmless local worker smoke test

Preview only:

```bash
uv run agent-backlog dispatch --db .agent-backlog/docs.sqlite3 --max-workers 2
```

Run one harmless worker:

```bash
uv run agent-backlog dispatch \
  --db .agent-backlog/docs.sqlite3 \
  --max-workers 1 \
  --backend subprocess \
  --command "bash examples/worker_echo.sh {task_id}"
```

Then inspect events:

```bash
uv run agent-backlog events --db .agent-backlog/docs.sqlite3 --limit 20
```

## Scenario 3: one-off task with per-task command

Per-task commands let a mixed queue carry different worker commands for different lanes or roles.

```bash
uv run agent-backlog enqueue \
  --db .agent-backlog/docs.sqlite3 \
  --title "Summarize one local docs page" \
  --body "Read one page and write a short note. Keep output local." \
  --lane docs \
  --role writer \
  --tag docs \
  --priority 10 \
  --workdir "$PWD" \
  --command "bash examples/worker_echo.sh {task_id}"

uv run agent-backlog dispatch \
  --db .agent-backlog/docs.sqlite3 \
  --backend subprocess \
  --max-workers 1
```

A dispatch-level `--command` overrides per-task commands when you want one temporary worker command for the whole run.

## Adapting the examples safely

Good starter lanes are `docs`, `quality`, `maintenance`, and `experiments`. Good starter tasks are checklists, notes, and deterministic local scripts.

When adapting examples:

- keep templates generic and free of secrets;
- pass a task id to the worker and let the worker read trusted local configuration;
- avoid command templates that execute task title/body text;
- set a timeout for subprocess workers;
- write runtime outputs under an ignored directory such as `.agent-backlog/`;
- inspect `status`, `events`, and `stale` before re-running workers.
