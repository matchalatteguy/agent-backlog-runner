# Changelog

## 0.2.0

- Claim a task and a capacity slot atomically before a subprocess starts.
- Record worker timeouts and spawn errors as `failed` instead of leaving tasks
  permanently `running`; continue the rest of the selected batch.
- Preserve operator status changes made while a worker is running.
- Deduplicate titles within a plan, reject stale plans, and apply tasks, events,
  and the scheduler cursor in one transaction.
- Refuse to overwrite a database's unsupported future schema version.
- Add a documentation audit demo that executes real workers, wheel smoke checks,
  and CI.

### API change

`apply_backlog_plan()` now leaves a plan marked `dry_run=True` unchanged. To
create tasks, explicitly call `plan_backlog(..., dry_run=False)` before applying
the plan. Applying an already applied plan, or one based on a changed queue,
raises `ValueError`; produce a fresh plan and try again.

The SQLite table layout and schema version remain unchanged at version 1.
`mark_task()` now returns a boolean; callers that previously ignored its return
value continue to work. The optional `expected_status` argument supports guarded
updates.
