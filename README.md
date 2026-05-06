# Typesafe Daggerverse

Shared Dagger modules.

Add as a dependency to another Dagger module:

```console
$ dagger install github.com/typesafe-ai/daggerverse/uv-workspace
$ dagger install github.com/typesafe-ai/daggerverse/github-status-monitor
```

## Modules

| Module | Description |
|--------|-------------|
| [`uv-workspace`](./uv-workspace) | Builds minimal project containers by parsing `uv.lock` to resolve local workspace dependencies, installing remote deps first for better caching, then local source. Supports extras, dependency groups, and automatic Dagger SDK codegen for modules that include a `dagger.json`. |
| [`github-status-monitor`](./github-status-monitor) | Blocks until every expected GitHub commit-status context on a given ref reaches a successful terminal state. Can auto-discovers Dagger Cloud checks via `dag.current_workspace().checks()`). |
