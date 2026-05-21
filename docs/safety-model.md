# Safety model

Agent Backlog Runner avoids surprising side effects:

- dry-run planning creates no tasks, files, directories, or subprocesses;
- identifiers are validated before writes;
- template catalogs reject missing and unknown fields;
- dispatch is opt-in and bounded;
- runtime databases and caches are ignored by version control;
- normal commands never delete directories.
