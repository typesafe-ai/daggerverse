# Typesafe Daggerverse

Shared Dagger modules.

Add as a dependency to another Dagger module:

```console
$ dagger install github.com/typesafe-ai/daggerverse/uv-workspace
$ dagger install github.com/typesafe-ai/daggerverse/wait-github-dagger-checks
```

## Modules

| Module | Description |
|--------|-------------|
| [`uv-workspace`](daggerverse/uv-workspace) | Builds minimal project containers by parsing `uv.lock` to resolve local workspace dependencies, installing remote deps first for better caching, then local source. Supports extras, dependency groups, and automatic Dagger SDK codegen for modules that include a `dagger.json`. |
| [`wait-github-dagger-checks`](daggerverse/wait-github-dagger-checks) | Blocks until every Dagger check defined by the consumer module has reported a successful GitHub commit status on a given ref. Polls the GitHub Statuses API with configurable timeouts, progress reporting, and optional fail-fast behaviour. |
