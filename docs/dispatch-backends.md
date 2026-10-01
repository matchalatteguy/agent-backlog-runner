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

Timeouts, spawn errors, and invalid working directories also mark the task
`failed`; they do not leave it `running` or abort the remaining selected batch.
Only the direct worker process is terminated on timeout. A command that starts
its own children must manage those children's cleanup.

## Concurrency cap

`--max-workers` caps total running work and the size of this dispatch batch. Tasks
run sequentially inside the CLI process. Each start reserves its task and a
capacity slot in one SQLite transaction, so competing local dispatchers cannot
start the same task. Use the same cap across dispatchers.

Final updates only replace `running`: an operator's cancellation or intervening
status change is preserved. Changing a status does not terminate a process. If
the CLI is killed, a task can remain `running`; inspect `stale` and its output
before requeueing it.

## Command guidance

Keep worker commands simple:

- pass all needed inputs through the task id, files, or environment variables you control;
- write outputs under a configured demo/runtime directory;
- prefer idempotent commands;
- set a timeout that matches the expected task size;
- inspect `status` and `events` after dispatch.

For detached session management and contained workspaces, see
[Local Agent Task Runtime](https://github.com/matchalatteguy/local-agent-task-runtime).
Long-running services and distributed worker pools are outside this package's scope.
