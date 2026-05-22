# Dispatch backends

Dispatch turns ready `todo` tasks into local work. It is deliberately opt-in and bounded: nothing runs unless the caller chooses a backend and a worker command.

## Dry-run backend

The default backend is `dry-run`:

```bash
uv run agent-backlog dispatch --db .agent-backlog/tasks.sqlite3 --max-workers 2
```

Dry-run dispatch records preview events and returns the task ids it would start. It does not execute subprocesses or change task status to `running`.

Use this mode first when checking a new backlog.

## Subprocess backend

The `subprocess` backend runs a local command template once for each selected task. It is synchronous: the CLI process waits for each selected command to finish or time out before moving to the next selected task.

```bash
uv run agent-backlog dispatch \
  --db .agent-backlog/tasks.sqlite3 \
  --max-workers 1 \
  --backend subprocess \
  --command "bash examples/worker_echo.sh {task_id}" \
  --timeout 60
```

`{task_id}`, `{title}`, `{lane}`, `{role}`, `{priority}`, and `{workdir}` are available as explicit placeholders. The template is split with shell-style quoting before placeholder values are substituted, so task metadata cannot create extra subprocess argv tokens. Unknown placeholders fail before any subprocess starts, which keeps command templates predictable. The command runs on the local machine, using the task's `workdir` when one is set. If it exits with code `0`, the task becomes `done`; otherwise it becomes `failed` and the final output is kept in the event log.

Tasks may carry their own `command` and `workdir`, either through the Python API or `agent-backlog enqueue --command ... --workdir ...`. A dispatch-level `--command` overrides per-task commands when you want one temporary worker command for the whole run.

## Concurrency cap

`--max-workers` caps total running work. If the store already has enough `running` tasks, dispatch skips new starts and reports that the cap was reached.

## Command guidance

Keep worker commands simple:

- pass all needed inputs through the task id, files, or environment variables you control;
- write outputs under a configured demo/runtime directory;
- prefer idempotent commands;
- set a timeout that matches the expected task size;
- inspect `status` and `events` after dispatch.

Long-running service management and distributed worker pools are outside this MVP.
