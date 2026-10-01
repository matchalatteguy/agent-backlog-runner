from __future__ import annotations

from dataclasses import dataclass

from .models import BacklogPolicy, TaskRecord, TaskTemplate
from .store import TaskStore
from .templates import TemplateCatalog, render_template


@dataclass(frozen=True)
class PlannedTask:
    template_slug: str
    title: str
    body: str
    priority: int
    lane: str
    tags: tuple[str, ...]
    role: str
    reason: str


@dataclass(frozen=True)
class BacklogPlan:
    active_depth: int
    target_depth: int
    planned: tuple[PlannedTask, ...]
    dry_run: bool = True
    cursor_advance: int = 0
    cursor_start: int = 0
    lanes: tuple[str, ...] = ()
    avoid_duplicates: bool = True


@dataclass(frozen=True)
class SchedulerState:
    cursor: int = 0


def plan_backlog(
    store: TaskStore,
    catalog: TemplateCatalog,
    policy: BacklogPolicy,
    state: SchedulerState | None = None,
    *,
    dry_run: bool = True,
) -> BacklogPlan:
    state = state or SchedulerState(cursor=store.get_state("cursor", 0))
    active_depth = store.active_depth(policy.lanes)
    if active_depth >= policy.min_queue_depth:
        return BacklogPlan(active_depth, policy.target_queue_depth, (), dry_run=dry_run)
    needed = min(policy.target_queue_depth - active_depth, policy.max_enqueue_per_cycle)
    candidates = list(catalog.select(policy.lanes, policy.tags))
    if not candidates or needed <= 0:
        return BacklogPlan(active_depth, policy.target_queue_depth, (), dry_run=dry_run)
    planned: list[PlannedTask] = []
    planned_titles: set[str] = set()
    index = state.cursor
    attempts = 0
    max_attempts = max(len(candidates) * 3, needed)
    while len(planned) < needed and attempts < max_attempts:
        template = candidates[index % len(candidates)]
        sequence = state.cursor + attempts + 1
        title, body = render_template(template, {"sequence": sequence, "slug": template.slug})
        if not policy.avoid_duplicates or (
            title not in planned_titles and not store.title_exists(title)
        ):
            planned.append(
                _planned_from_template(template, title, body, "queue depth below minimum")
            )
            planned_titles.add(title)
        index += 1
        attempts += 1
    return BacklogPlan(
        active_depth,
        policy.target_queue_depth,
        tuple(planned),
        dry_run=dry_run,
        cursor_advance=attempts,
        cursor_start=state.cursor,
        lanes=policy.lanes,
        avoid_duplicates=policy.avoid_duplicates,
    )


def _planned_from_template(
    template: TaskTemplate, title: str, body: str, reason: str
) -> PlannedTask:
    return PlannedTask(
        template_slug=template.slug,
        title=title,
        body=body,
        priority=template.priority,
        lane=template.lane,
        tags=template.tags,
        role=template.role,
        reason=reason,
    )


def apply_backlog_plan(
    store: TaskStore, plan: BacklogPlan, state: SchedulerState | None = None
) -> list[TaskRecord]:
    if plan.dry_run or not plan.planned:
        return []
    created = []
    with store.transaction():
        cursor = store.get_state("cursor", 0)
        if cursor != plan.cursor_start or (state and state.cursor != plan.cursor_start):
            raise ValueError("backlog plan is stale: scheduler cursor changed; plan again")
        if store.active_depth(plan.lanes) != plan.active_depth:
            raise ValueError("backlog plan is stale: queue depth changed; plan again")
        for item in plan.planned:
            if plan.avoid_duplicates and store.title_exists(item.title):
                raise ValueError("backlog plan is stale: task title already exists; plan again")
            created.append(
                store.create_task(
                    title=item.title,
                    body=item.body,
                    priority=item.priority,
                    lane=item.lane,
                    tags=item.tags,
                    role=item.role,
                    event_message=f"planned from template {item.template_slug}: {item.reason}",
                )
            )
        store.set_state("cursor", cursor + plan.cursor_advance)
    return created
