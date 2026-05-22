"""Local SQLite backlog runner for bounded agent task queues."""

from .config import DEFAULT_DB_PATH, ENV_DB_PATH, RunnerConfig, resolve_db_path
from .dispatch import (
    DispatchPolicy,
    DispatchResult,
    dispatch_ready,
    render_command_argv,
    render_command_template,
)
from .models import BacklogPolicy, TaskEvent, TaskRecord, TaskStatus, TaskTemplate
from .scheduler import BacklogPlan, SchedulerState, apply_backlog_plan, plan_backlog
from .status import StatusSnapshot, get_status_snapshot
from .store import SCHEMA_VERSION, TaskStore, init_store
from .templates import TemplateCatalog, load_template_catalog

__all__ = [
    "BacklogPlan",
    "BacklogPolicy",
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
    "get_status_snapshot",
    "init_store",
    "load_template_catalog",
    "plan_backlog",
    "render_command_argv",
    "render_command_template",
    "resolve_db_path",
]
