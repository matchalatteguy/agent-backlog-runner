# Template schema

Template catalogs are YAML or JSON files with a top-level `templates` list. Required fields are `slug`, `title`, and `body`. Optional fields are `role`, `priority`, `lane`, `tags`, and `acceptance`.

Slugs, roles, lanes, and tags must use lowercase letters, numbers, `-`, or `_`. Unknown fields are rejected so mistakes do not silently create malformed tasks.
