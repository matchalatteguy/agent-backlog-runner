"""Local SQLite backlog runner for bounded agent task queues."""

from .batches import BatchManifest, BatchTask, enqueue_batch, load_batch_manifest
from .config import DEFAULT_DB_PATH, ENV_DB_PATH, RunnerConfig, resolve_db_path
from .dispatch import (
    DispatchPolicy,
    DispatchResult,
    dispatch_ready,
    render_command_argv,
    render_command_template,
)
from .models import BacklogPolicy, TaskEvent, TaskRecord, TaskStatus, TaskTemplate
from .recovery import recover_task
from .reports import write_queue_report
from .scheduler import BacklogPlan, SchedulerState, apply_backlog_plan, plan_backlog
from .status import StatusSnapshot, get_status_snapshot
from .store import SCHEMA_VERSION, TaskStore, init_store
from .templates import TemplateCatalog, load_template_catalog

__all__ = [
    "BacklogPlan",
    "BacklogPolicy",
    "BatchManifest",
    "BatchTask",
    "DEFAULT_DB_PATH",
    "DispatchPolicy",
    "DispatchResult",
    "ENV_DB_PATH",
    "RunnerConfig",
    "SCHEMA_VERSION",
    "SchedulerState",
    "StatusSnapshot",
    "TaskEvent",
    "TaskRecord",
    "TaskStatus",
    "TaskStore",
    "TaskTemplate",
    "TemplateCatalog",
    "apply_backlog_plan",
    "dispatch_ready",
    "enqueue_batch",
    "get_status_snapshot",
    "init_store",
    "load_template_catalog",
    "load_batch_manifest",
    "plan_backlog",
    "render_command_argv",
    "render_command_template",
    "resolve_db_path",
    "recover_task",
    "write_queue_report",
]
