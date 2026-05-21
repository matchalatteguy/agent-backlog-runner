from agent_backlog_runner.models import BacklogPolicy
from agent_backlog_runner.scheduler import apply_backlog_plan, plan_backlog
from agent_backlog_runner.store import init_store
from agent_backlog_runner.templates import load_template_catalog


def _catalog(tmp_path):
    path = tmp_path / "templates.yaml"
    path.write_text("""
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
""", encoding="utf-8")
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
    plan = plan_backlog(store, catalog, BacklogPolicy(min_queue_depth=1, target_queue_depth=2))
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
