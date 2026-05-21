# Template schema

Template catalogs are YAML or JSON files with a top-level `templates` list. Each template describes a synthetic task card that the scheduler may render when active queue depth drops below policy.

## Minimal YAML

```yaml
templates:
  - slug: docs-refresh
    title: "Refresh docs section ${sequence}"
    body: "Review one documentation page and write a short improvement note."
```

## Fields

Required fields:

- `slug`: stable template id. Use lowercase letters, numbers, `-`, or `_`.
- `title`: task title template. `${sequence}` is replaced with the next sequence number.
- `body`: task body template. `${sequence}` is also available here.

Optional fields:

- `role`: who should handle the work, such as `writer`, `tester`, or `maintainer`. Defaults to `agent`.
- `priority`: integer priority. Defaults to `0`.
- `lane`: queue lane, such as `docs`, `quality`, or `maintenance`. Defaults to `default`.
- `tags`: list of lowercase tag strings.
- `acceptance`: list of short completion checks.

Unknown fields are rejected. This keeps typos from becoming silently ignored configuration.

## Validation rules

`slug`, `role`, `lane`, and each tag must contain only lowercase letters, numbers, `-`, or `_`.

The catalog must contain at least one template. The loader fails closed for malformed YAML/JSON, missing required fields, unknown fields, or invalid identifiers.

Validate a catalog before running the scheduler:

```bash
uv run agent-backlog templates validate --templates examples/templates/basic-backlog.yaml
```

## Filtering

Scheduler commands can filter by lane and tag:

```bash
uv run agent-backlog scheduler plan \
  --db .agent-backlog/tasks.sqlite3 \
  --templates examples/templates/basic-backlog.yaml \
  --lane docs \
  --tag quality \
  --dry-run
```

When filters are present, only templates matching at least one requested lane and all requested tags are eligible.
