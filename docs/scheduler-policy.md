# Scheduler policy

The scheduler counts active tasks (`todo` and `running`). If active depth is below `min_queue_depth`, it plans enough new tasks to approach `target_queue_depth` without exceeding `max_enqueue_per_cycle`.

`scheduler plan` previews without side effects. `scheduler run` applies the plan and advances the cursor in SQLite.
