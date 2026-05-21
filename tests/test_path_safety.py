import pytest

from agent_backlog_runner.safety import require_safe_task_id, safe_child_path


def test_task_id_validation():
    assert require_safe_task_id("task_abcdef123456") == "task_abcdef123456"
    with pytest.raises(ValueError):
        require_safe_task_id("../bad")


def test_safe_child_path_rejects_escape_and_absolute(tmp_path):
    assert safe_child_path(tmp_path, "child/file.txt").parent.name == "child"
    with pytest.raises(ValueError):
        safe_child_path(tmp_path, "../escape")
    with pytest.raises(ValueError):
        safe_child_path(tmp_path, "/tmp/absolute")
