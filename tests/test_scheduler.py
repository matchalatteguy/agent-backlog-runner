import pytest

from agent_backlog_runner.models import BacklogPolicy
from agent_backlog_runner.scheduler import apply_backlog_plan, plan_backlog
from agent_backlog_runner.store import init_store
from agent_backlog_runner.templates import load_template_catalog


def _catalog(tmp_path):
    path = tmp_path / "templates.yaml"
    path.write_text(
        """
templates:
  - slug: docs-refresh
    title: "Refresh docs ${sequence}"
    body: "Review docs"
    lane: docs
    tags: [docs]
  - slug: test-hardening
    title: "Harden tests ${sequence}"
    body: "Review tests"
    lane: quality
    tags: [tests]
""",
        encoding="utf-8",
    )
    return load_template_catalog(path)


def test_plan_respects_depth_and_caps(tmp_path):
    store = init_store(tmp_path / "tasks.sqlite3")
    plan = plan_backlog(
        store,
        _catalog(tmp_path),
        BacklogPolicy(min_queue_depth=2, target_queue_depth=5, max_enqueue_per_cycle=2),
    )
    assert len(plan.planned) == 2
    assert store.list_tasks() == []
    store.close()


def test_apply_plan_persists_tasks_and_cursor(tmp_path):
    store = init_store(tmp_path / "tasks.sqlite3")
    catalog = _catalog(tmp_path)
    plan = plan_backlog(
        store,
        catalog,
        BacklogPolicy(min_queue_depth=1, target_queue_depth=2),
        dry_run=False,
    )
    created = apply_backlog_plan(store, plan)
    assert len(created) == 2
    assert store.active_depth() == 2
    assert store.get_state("cursor") == 2
    store.close()


def test_lane_filter_selects_expected_templates(tmp_path):
    store = init_store(tmp_path / "tasks.sqlite3")
    plan = plan_backlog(
        store,
        _catalog(tmp_path),
        BacklogPolicy(min_queue_depth=1, target_queue_depth=1, lanes=("quality",)),
    )
    assert plan.planned[0].lane == "quality"
    store.close()


def test_duplicate_skips_advance_cursor_to_next_template(tmp_path):
    store = init_store(tmp_path / "tasks.sqlite3")
    catalog = _catalog(tmp_path)
    store.create_task(title="Refresh docs 1", body="already queued")

    plan = plan_backlog(
        store,
        catalog,
        BacklogPolicy(min_queue_depth=2, target_queue_depth=3, max_enqueue_per_cycle=1),
        dry_run=False,
    )

    assert [item.title for item in plan.planned] == ["Harden tests 2"]
    apply_backlog_plan(store, plan)
    assert store.get_state("cursor") == 2
    store.close()


def test_dry_run_plan_cannot_create_tasks_or_advance_cursor(tmp_path):
    with init_store(tmp_path / "tasks.sqlite3") as store:
        plan = plan_backlog(store, _catalog(tmp_path), BacklogPolicy())
        assert plan.planned
        assert apply_backlog_plan(store, plan) == []
        assert store.list_tasks() == []
        assert store.events() == []
        assert store.get_state("cursor") == 0


def test_constant_titles_are_deduplicated_within_plan(tmp_path):
    path = tmp_path / "templates.yaml"
    path.write_text("templates:\n  - slug: docs\n    title: Constant title\n    body: Check docs\n")
    with init_store(tmp_path / "tasks.sqlite3") as store:
        plan = plan_backlog(store, load_template_catalog(path), BacklogPolicy(target_queue_depth=3))
        assert [item.title for item in plan.planned] == ["Constant title"]


def test_plan_cannot_be_applied_twice(tmp_path):
    with init_store(tmp_path / "tasks.sqlite3") as store:
        plan = plan_backlog(store, _catalog(tmp_path), BacklogPolicy(), dry_run=False)
        created = apply_backlog_plan(store, plan)
        with pytest.raises(ValueError, match="stale"):
            apply_backlog_plan(store, plan)
        assert len(store.list_tasks()) == len(created)


def test_partial_plan_failure_rolls_back_tasks_events_and_cursor(tmp_path, monkeypatch):
    with init_store(tmp_path / "tasks.sqlite3") as store:
        plan = plan_backlog(store, _catalog(tmp_path), BacklogPolicy(), dry_run=False)
        create = store.create_task
        calls = 0

        def fail_second(**kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("database write failed")
            return create(**kwargs)

        monkeypatch.setattr(store, "create_task", fail_second)
        with pytest.raises(RuntimeError, match="database write failed"):
            apply_backlog_plan(store, plan)
        assert store.list_tasks() == []
        assert store.events() == []
        assert store.get_state("cursor") == 0
