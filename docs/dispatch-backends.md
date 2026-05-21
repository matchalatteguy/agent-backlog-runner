# Dispatch backends

The default backend is `dry-run`, which records preview events only. The `subprocess` backend runs a local command template with `{task_id}` substituted. Concurrency is capped by `--max-workers`.

Keep command templates simple and local. Long-running service supervision is outside the MVP.
