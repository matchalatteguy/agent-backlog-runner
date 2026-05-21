# Status and heartbeats

Status reports include counts by task state, active worker count, stale running tasks, and recent events. A running task is stale when the latest heartbeat or status update is older than the configured threshold.

Library callers can use `record_heartbeat(store, task_id, payload)` to keep long tasks fresh.
