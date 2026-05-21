# Safety model

Agent Backlog Runner is built for local, inspectable automation. It should be safe to try in a checkout without credentials or network access.

## Boundaries

- Dry-run planning creates no tasks, files, directories, or subprocesses.
- Template catalogs reject missing fields, unknown fields, and unsafe identifiers.
- Dispatch is opt-in and bounded by `--max-workers`.
- Runtime databases and caches are ignored by version control.
- Normal commands never delete directories.
- The MVP has no hosted service integration and no credential requirement.

## Fail-closed behavior

The CLI exits non-zero on malformed templates, invalid policy values, unreadable inputs, unsafe identifiers, and subprocess configuration errors. It should be obvious when the runner did not understand an input.

## Local state

Use a project-local runtime directory such as `.agent-backlog/`:

```bash
uv run agent-backlog init --db .agent-backlog/tasks.sqlite3
```

That directory is disposable demo/runtime state and is ignored by version control. To restart a walkthrough from scratch without changing the existing demo store, choose a new DB path such as `.agent-backlog/tasks-2.sqlite3`.

## Dispatch safety

The dry-run backend is the default. The subprocess backend requires an explicit command template:

```bash
uv run agent-backlog dispatch \
  --db .agent-backlog/tasks.sqlite3 \
  --backend subprocess \
  --command "bash examples/worker_echo.sh {task_id}"
```

Prefer commands that are deterministic, bounded, and easy to inspect. Avoid commands that require external accounts, mutate unrelated directories, or depend on hidden machine state.

## Public examples

The packaged examples are intentionally synthetic: documentation refresh, test hardening, and dependency note review. They are examples of queue mechanics, not recommendations for a specific business domain.
