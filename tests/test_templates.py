import pytest

from agent_backlog_runner.templates import load_template_catalog, render_template


def test_valid_template_catalog_loads(tmp_path):
    path = tmp_path / "templates.yaml"
    path.write_text(
        """
templates:
  - slug: docs-refresh
    title: "Refresh ${sequence}"
    body: "Review a page"
    role: writer
    lane: docs
    tags: [docs]
""",
        encoding="utf-8",
    )
    catalog = load_template_catalog(path)
    assert len(catalog.templates) == 1
    assert render_template(catalog.templates[0], {"sequence": 7})[0] == "Refresh 7"


def test_unknown_template_fields_fail(tmp_path):
    path = tmp_path / "templates.yaml"
    path.write_text(
        """
templates:
  - slug: docs-refresh
    title: "Refresh"
    body: "Review"
    surprise: true
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unknown"):
        load_template_catalog(path)


def test_unsafe_template_slug_fails(tmp_path):
    path = tmp_path / "templates.yaml"
    path.write_text(
        """
templates:
  - slug: Bad Slug
    title: "Refresh"
    body: "Review"
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unsafe"):
        load_template_catalog(path)
