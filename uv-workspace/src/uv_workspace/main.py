"""UvWorkspace: build minimal containers by parsing uv.lock for local dependencies."""

import posixpath
import tomllib
from collections import OrderedDict, deque
from typing import Annotated

import dagger
from dagger import Doc, dag, field, function, object_type


def _parse_local_packages(lock_data: dict) -> OrderedDict[str, str]:
    """Return {package_name: local_path} for all local packages (editable or directory).

    Results are sorted by package name for deterministic build order.
    """
    result = {}
    for pkg in lock_data.get("package", []):
        source = pkg.get("source", {})
        if "editable" in source:
            result[pkg["name"]] = source["editable"]
        elif "directory" in source:
            result[pkg["name"]] = source["directory"]
    return OrderedDict(sorted(result.items()))


def _find_transitive_local_deps(lock_data: dict, project: str) -> OrderedDict[str, str]:
    """Find all local packages that `project` transitively depends on (including itself).

    Results are sorted by package name for deterministic build order.
    """
    locals_ = _parse_local_packages(lock_data)

    dep_graph: dict[str, list[str]] = {}
    for pkg in lock_data.get("package", []):
        name = pkg["name"]
        deps = {d["name"] for d in pkg.get("dependencies", [])}
        for group_deps in pkg.get("dev-dependencies", {}).values():
            deps |= {d["name"] for d in group_deps}
        dep_graph[name] = sorted(deps)

    needed: dict[str, str] = {}
    queue: deque[str] = deque()

    if project in locals_:
        needed[project] = locals_[project]
        queue.append(project)

    visited = {project}
    while queue:
        current = queue.popleft()
        for dep in dep_graph.get(current, []):
            if dep not in visited:
                visited.add(dep)
                if dep in locals_:
                    needed[dep] = locals_[dep]
                    queue.append(dep)

    return OrderedDict(sorted(needed.items()))


@object_type
class UvWorkspace:
    """Builds minimal project containers by parsing uv.lock to resolve local dependencies."""

    source_dir: Annotated[
        dagger.Directory,
        Doc("Source directory containing the workspace"),
    ] = field()

    base_container: Annotated[
        dagger.Container,
        Doc("Pre-configured container (with auth, system packages, etc.)"),
    ] = field()

    workspace_path: Annotated[
        str,
        Doc(
            "Path to workspace root (holding uv.lock and pyproject.toml) within source_dir"
        ),
    ] = field(default=".")

    @function
    async def build(
        self,
        package: Annotated[
            str | None,
            Doc(
                "Package name; if set, only that package's transitive local deps are installed"
            ),
        ] = None,
    ) -> dagger.Container:
        """Build a minimal container with deps installed for the given package.

        Parses uv.lock to find local workspace dependencies, then builds
        in two layers: remote deps first (cacheable), then local source.
        If package is specified, only that package's deps are installed (for workspaces).
        """
        ws_dir = (
            self.source_dir
            if self.workspace_path == "."
            else self.source_dir.directory(self.workspace_path)
        )
        pyproject_toml = ws_dir.file("pyproject.toml")
        uv_lock = ws_dir.file("uv.lock")

        lock_data = tomllib.loads(await uv_lock.contents())
        all_local = _parse_local_packages(lock_data)
        needed_local = (
            _find_transitive_local_deps(lock_data, package) if package else all_local
        )

        ctr = self.base_container
        workdir = await ctr.workdir()

        ctr = (
            ctr.with_env_variable("UV_PROJECT_ENVIRONMENT", "/usr/local")
            .with_mounted_cache("/root/.cache/uv", dag.cache_volume("uv-cache"))
            .with_env_variable("UV_LINK_MODE", "copy")
            .with_env_variable("UV_FROZEN", "1")
        )

        ctr = ctr.with_file(
            posixpath.join(workdir, "pyproject.toml"), pyproject_toml
        ).with_file(posixpath.join(workdir, "uv.lock"), uv_lock)

        for pkg_name, path in sorted(needed_local.items()):
            src_name = pkg_name.replace("-", "_")
            ctr = (
                ctr.with_file(
                    posixpath.join(workdir, path, "pyproject.toml"),
                    ws_dir.file(posixpath.join(path, "pyproject.toml")),
                )
                .with_new_file(posixpath.join(workdir, path, "README.md"), "")
                .with_new_file(
                    posixpath.join(workdir, path, "src", src_name, "__init__.py"), ""
                )
            )

        uv_sync_base = ["uv", "sync", "--all-extras", "--dev"]
        if package:
            uv_sync_base += ["--package", package]

        ctr = ctr.with_exec([*uv_sync_base, "--no-install-local"])

        for path in sorted(needed_local.values()):
            ctr = ctr.with_directory(
                posixpath.join(workdir, path, "src"),
                ws_dir.directory(posixpath.join(path, "src")),
            )

        ctr = ctr.with_exec(uv_sync_base)

        return ctr
