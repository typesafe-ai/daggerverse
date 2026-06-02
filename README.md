# Typesafe Daggerverse

Shared Dagger modules.

Add as a dependency to another Dagger module:

```console
$ dagger install github.com/typesafe-ai/daggerverse/uv
$ dagger install github.com/typesafe-ai/daggerverse/github-status-monitor
$ dagger install github.com/typesafe-ai/daggerverse/twingate
$ dagger install github.com/typesafe-ai/daggerverse/pinact
$ dagger install github.com/typesafe-ai/daggerverse/zizmor
```

## Modules

| Module | Description |
|--------|-------------|
| [`uv`](./uv) | Tooling for [uv](https://github.com/astral-sh/uv)-managed Python projects: audit locked dependencies (`uv audit`) across every workspace, build minimal containers with a package's deps installed (parsing `uv.lock` to resolve local members, remote deps first for caching, with extras/groups and Dagger SDK codegen), and build/export relocatable virtual environments that run in a fresh container with no Python of its own. |
| [`github-status-monitor`](./github-status-monitor) | Blocks until every expected GitHub commit-status context on a given ref reaches a successful terminal state. Can auto-discovers Dagger Cloud checks via `dag.current_workspace().checks()`). |
| [`twingate`](./twingate) | Twingate HTTP CONNECT proxy as a Dagger service. Runs the client in userspace networking mode (`--tun off`) so no root or `NET_ADMIN` is required. Authenticate with a service key, then bind the proxy to any container via `bind_proxy`. |
| [`pinact`](./pinact) | Check that GitHub Actions are pinned to full-length commit SHAs using [pinact](https://github.com/suzuki-shunsuke/pinact). Runs in check-only mode (`--check`). Optionally pass a GitHub token for API-based SHA resolution and comment verification. |
| [`zizmor`](./zizmor) | Static analysis of GitHub Actions for security issues using [zizmor](https://github.com/woodruffw/zizmor). Detects template injection, credential leaks, excessive permissions, and more. Runs offline by default; pass a GitHub token for online audits. |
