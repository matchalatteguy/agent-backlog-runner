# Scheduler policy

The scheduler keeps a local backlog above a configured depth by rendering tasks from a validated template catalog.

## Active depth

Active depth counts tasks in these statuses:

- `todo`
- `running`

Tasks in `blocked`, `done`, `failed`, or `cancelled` do not count toward active depth.

## Policy fields

- `min_queue_depth`: planning starts only when active depth is below this value.
- `target_queue_depth`: the planner tries to reach this active depth.
- `max_enqueue_per_cycle`: hard cap for how many tasks may be created in one run.
- `lanes`: optional lane filter.
- `tags`: optional tag filter.
- `avoid_duplicates`: skips titles that already exist when enabled by library callers.

Example:

```text
active depth: 1
min depth:    2
target depth: 4
max enqueue:  3
planned:      3
```

The planner needs three tasks to reach target depth, and the per-cycle cap allows three, so it plans three.

## Preview before writes

Use `scheduler plan` to inspect work before creating rows:

```bash
uv run agent-backlog scheduler plan \
  --db .agent-backlog/tasks.sqlite3 \
  --templates examples/templates/basic-backlog.yaml \
  --min-depth 2 \
  --target-depth 4 \
  --max-enqueue 3 \
  --dry-run \
  --json
```

The preview has no side effects. It does not create tasks, advance state, create files, or start workers.

## Apply the plan

Use `scheduler run` to create tasks:

```bash
uv run agent-backlog scheduler run \
  --db .agent-backlog/tasks.sqlite3 \
  --templates examples/templates/basic-backlog.yaml \
  --min-depth 2 \
  --target-depth 4 \
  --max-enqueue 3
```

The CLI prints the number of tasks created. Each created task gets a durable event row, so `agent-backlog events` can explain what happened.

## Determinism

Task rendering is deterministic for the same store state, template catalog, and policy. Sequence numbers advance through the store, which means repeated runs add new synthetic cards instead of rewriting old ones.
