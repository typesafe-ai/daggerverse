# Typesafe Daggerverse

Shared Dagger modules.

Install into a Dagger workspace (or add to a module with `dagger module client add`):

```console
$ dagger install github.com/typesafe-ai/daggerverse/uv
$ dagger install github.com/typesafe-ai/daggerverse/pyproject
$ dagger install github.com/typesafe-ai/daggerverse/ruff
$ dagger install github.com/typesafe-ai/daggerverse/github
$ dagger install github.com/typesafe-ai/daggerverse/twingate
$ dagger install github.com/typesafe-ai/daggerverse/zizmor
$ dagger install github.com/typesafe-ai/daggerverse/deptry
$ dagger install github.com/typesafe-ai/daggerverse/k3s
```

## Modules

| Module | Description |
|--------|-------------|
| [`uv`](./uv) | Build and audit [uv](https://github.com/astral-sh/uv)-managed Python projects. Builds minimal containers for a workspace package: it reads `uv.lock` to find the local members the package needs, installs remote dependencies first so they cache, and supports extras and groups. Exports relocatable virtual environments that run in a fresh container with no Python of its own. Audits locked dependencies with `uv audit`, one check per workspace. |
| [`pyproject`](./pyproject) | Discover the Python projects (`pyproject.toml`) and workspaces (`uv.lock`, `pylock.toml`, or any configured marker) in a Dagger workspace. Each comes with the scoped source it needs: its own files plus its local path dependencies, wherever they live, without unrelated nested projects. A workspace also exposes just its dependency manifests, for a dependency layer that source edits don't invalidate. |
| [`ruff`](./ruff) | Lint and format Python code with [Ruff](https://docs.astral.sh/ruff/). The ruff version comes from the workspace's `uv.lock`, `ruff.toml` or `pyproject.toml`. |
| [`github`](./github) | GitHub utilities for Dagger pipelines. Includes a status monitor that polls commit statuses and check runs until a final verdict is reached. |
| [`twingate`](./twingate) | Twingate HTTP CONNECT proxy as a Dagger service. Runs the client in userspace networking mode (`--tun off`) so no root or `NET_ADMIN` is required. Authenticate with a service key, then bind the proxy to any container via `bind_proxy`. |
| [`zizmor`](./zizmor) | Static analysis of GitHub Actions for security issues using [zizmor](https://github.com/woodruffw/zizmor). Detects template injection, credential leaks, excessive permissions, and more. Runs offline by default; pass a GitHub token for online audits. |
| [`deptry`](./deptry) | Find unused, missing, misplaced, and transitive Python dependencies with [deptry](https://deptry.com). Each Python project (`pyproject.toml`) in the source tree is its own check, and the checks run in parallel. |
| [`k3s`](./k3s) | Run an isolated K3s Kubernetes API as a Dagger service and return its kubeconfig. |
