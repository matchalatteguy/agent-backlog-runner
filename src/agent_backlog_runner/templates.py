from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from string import Template
from typing import Any

import yaml

from .models import TaskTemplate
from .safety import require_safe_slug

_ALLOWED_FIELDS = {"slug", "title", "body", "role", "priority", "lane", "tags", "acceptance"}
_REQUIRED_FIELDS = {"slug", "title", "body"}


@dataclass(frozen=True)
class TemplateCatalog:
    templates: tuple[TaskTemplate, ...]

    def select(self, lanes: tuple[str, ...] = (), tags: tuple[str, ...] = ()) -> tuple[TaskTemplate, ...]:
        selected = []
        tag_set = set(tags)
        for template in self.templates:
            if lanes and template.lane not in lanes:
                continue
            if tag_set and not tag_set.intersection(template.tags):
                continue
            selected.append(template)
        return tuple(selected)


def _load_data(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        return json.loads(text)
    return yaml.safe_load(text)


def _as_list(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, dict) and isinstance(data.get("templates"), list):
        data = data["templates"]
    if not isinstance(data, list):
        raise ValueError("template catalog must be a list or object with templates list")
    if not all(isinstance(item, dict) for item in data):
        raise ValueError("each template must be an object")
    return data


def _tuple_of_strings(value: Any, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{field} must be a list of strings")
    return tuple(value)


def parse_template(item: dict[str, Any]) -> TaskTemplate:
    unknown = set(item) - _ALLOWED_FIELDS
    missing = _REQUIRED_FIELDS - set(item)
    if unknown:
        raise ValueError(f"unknown template fields: {sorted(unknown)}")
    if missing:
        raise ValueError(f"missing template fields: {sorted(missing)}")
    slug = require_safe_slug(str(item["slug"]))
    lane = require_safe_slug(str(item.get("lane", "default")), field="lane")
    role = require_safe_slug(str(item.get("role", "agent")), field="role")
    tags = tuple(require_safe_slug(tag, field="tag") for tag in _tuple_of_strings(item.get("tags"), "tags"))
    acceptance = _tuple_of_strings(item.get("acceptance"), "acceptance")
    try:
        priority = int(item.get("priority", 0))
    except (TypeError, ValueError) as exc:
        raise ValueError("priority must be an integer") from exc
    return TaskTemplate(
        slug=slug,
        title=str(item["title"]),
        body=str(item["body"]),
        role=role,
        priority=priority,
        lane=lane,
        tags=tags,
        acceptance=acceptance,
    )


def load_template_catalog(path: str | Path) -> TemplateCatalog:
    p = Path(path)
    data = _as_list(_load_data(p))
    templates = tuple(parse_template(item) for item in data)
    slugs = [template.slug for template in templates]
    if len(slugs) != len(set(slugs)):
        raise ValueError("template slugs must be unique")
    return TemplateCatalog(templates)


def render_template(template: TaskTemplate, variables: dict[str, object]) -> tuple[str, str]:
    safe_vars = {key: str(value) for key, value in variables.items()}
    return Template(template.title).safe_substitute(safe_vars), Template(template.body).safe_substitute(safe_vars)
