import posixpath
import tomllib
from collections import OrderedDict
from typing import Annotated, Self

from dagger import Doc, field, object_type
from dagger.telemetry import get_tracer
from dagger_clients.core import Directory

from uv.utils import (
    build_uv_sync_args,
    find_transitive_local_deps,
    normalize_package_name,
    parse_local_packages,
    parse_project_name,
    require_package_selection,
    selected_groups,
)
from uv.workspace.build_backends import PackageBackend
from uv.workspace.build_backends.plugin import BuildBackend, BuildBackendLayout


def _match_reachable(
    packages: OrderedDict[str, str],
    workspace_path: str,
    pyproject_paths: list[str],
) -> OrderedDict[str, str]:
    """Keep only local packages whose resolved path has a pyproject.toml in the source tree."""
    existing = {posixpath.normpath(posixpath.dirname(p)) for p in pyproject_paths}
    result = OrderedDict()
    for name, path in packages.items():
        resolved = posixpath.normpath(posixpath.join(workspace_path, path))
        if resolved in existing:
            result[name] = path
    return result


_MODULE_NAME_OVERRIDES: dict[str, str] = {
    "dagger-io": "dagger",
}


def _module_name(pkg_name: str) -> str:
    return _MODULE_NAME_OVERRIDES.get(pkg_name, pkg_name.replace("-", "_"))


def _transitive_union(lock_data: dict, packages: list[str], groups: set[str] | None) -> OrderedDict[str, str]:
    merged: OrderedDict[str, str] = OrderedDict()
    for package in packages:
        merged.update(find_transitive_local_deps(lock_data, package, groups))
    return OrderedDict(sorted(merged.items()))


async def _discover_local_packages(
    lock_data: dict,
    packages: list[str],
    all_packages: bool,
    default_package: str | None,
    groups: set[str] | None,
    workspace_path: str,
    manifests_dir: Directory,
) -> tuple[OrderedDict[str, str], OrderedDict[str, str]]:
    """Find the reachable local packages: `(all_local, needed_local)`.

    Reads only the manifests (lockfiles and `pyproject.toml` files), so it never
    loads package sources.

    `needed_local` mirrors `uv sync`'s selection: every member when
    `all_packages` is set, the union of the explicit `packages`' transitive
    local deps when given, otherwise the transitive local deps of
    `default_package` (the current package, à la a bare `uv sync`). Only the
    targets' own selected dependency `groups` are followed.
    """
    with get_tracer().start_as_current_span("discover local packages") as span:
        # Glob the pyproject.toml paths once and reuse them for both all-local and
        # needed-local reachability filtering.
        pyproject_paths = await manifests_dir.glob("**/pyproject.toml")
        all_local = _match_reachable(parse_local_packages(lock_data), workspace_path, pyproject_paths)
        if all_packages:
            needed_local = all_local
        else:
            # create()'s require_package_selection guarantees a target here:
            # the explicit packages, or the current (workspace-root) package.
            targets = packages or ([default_package] if default_package else [])
            needed_local = _match_reachable(
                _transitive_union(lock_data, targets, groups), workspace_path, pyproject_paths
            )

        span.set_attribute("packages.all", len(all_local))
        span.set_attribute("packages.needed", len(needed_local))
        span.set_attribute("packages.names", sorted(all_local))

    return all_local, needed_local


@object_type
class ProjectSource:
    """A project's files: the project and its local dependencies, at workspace-root-relative paths."""

    path: Annotated[str, Doc("Project root, relative to the workspace root")] = field()
    source: Annotated[Directory, Doc("The project's files and those of its local dependencies")] = field()


@object_type
class LocalPackage:
    """A local (editable/directory/virtual) package in a uv workspace.

    ``pyproject_contents`` is captured while resolving the plan so scaffold
    layers depend on package metadata, not on the whole source directory.
    """

    name: Annotated[str, Doc("Package name")] = field()
    path: Annotated[str, Doc("Workspace-relative path")] = field()
    module: Annotated[str, Doc("Python import name; dotted for a module inside namespace packages")] = field()
    flat: Annotated[
        bool,
        Doc("Flat layout (module at package root) vs src layout (module under src/)"),
    ] = field(default=False)
    pyproject_contents: Annotated[
        str,
        Doc("Already-read package metadata used when creating a dependency scaffold"),
    ] = field()
    source: Annotated[
        Directory,
        Doc("The package's files and those of its local dependencies, at workspace-root-relative paths"),
    ] = field()
    layout: Annotated[
        BuildBackendLayout | None,
        Doc("Layout resolved from a `BuildBackend` plug-in, replacing built-in backend settings"),
    ] = field(default=None)

    @property
    def build_backend(self) -> PackageBackend:
        """Build backend and its settings, derived from the captured package metadata."""
        return PackageBackend.from_pyproject(tomllib.loads(self.pyproject_contents), self.name, self.layout)

    @property
    def module_paths(self) -> list[str]:
        """Package-relative import directories (or `.py` modules) used to scaffold modules."""
        paths = self.build_backend.module_paths
        if paths is not None:
            return paths
        module_path = self.module.replace(".", "/")
        return [module_path if self.flat else posixpath.join("src", module_path)]

    @property
    def source_paths(self) -> list[str]:
        """Copy source roots, or only declared modules for a flat layout."""
        paths = self.build_backend.source_paths
        if paths is not None:
            return paths
        return [self.module.replace(".", "/")] if self.flat else ["src"]

    @property
    def build_inputs(self) -> list[str]:
        """Package-relative files (or globs) staged into the scaffold for the backend."""
        return self.build_backend.build_inputs


@object_type
class UvSyncPlan:
    """Resolved build configuration for a uv workspace sync."""

    ws_dir: Annotated[
        Directory,
        Doc("The workspace root's manifests: uv.lock, uv.toml and pyproject.toml files"),
    ] = field()

    source_dir: Annotated[
        Directory,
        Doc("Every member's files; read only for files outside the needed packages, e.g. symlink targets"),
    ] = field()

    workspace_path: Annotated[
        str,
        Doc("Workspace path within the source directory"),
    ] = field(default=".")

    all_local: Annotated[
        list[LocalPackage],
        Doc("Every local package in the workspace (sorted by name)"),
    ] = field()

    needed_local: Annotated[
        list[LocalPackage],
        Doc("Local packages the target transitively depends on (sorted by name)"),
    ] = field()

    flat_packages: Annotated[
        list[str],
        Doc("Virtual/deps-only packages: pyproject scaffolded, source skipped"),
    ] = field(default=list)

    uv_sync_args: Annotated[
        list[str],
        Doc("Precomputed uv sync argv"),
    ] = field()

    no_editable: Annotated[
        bool,
        Doc("Local packages installed non-editable (source baked into site-packages, not path-linked)"),
    ] = field(default=False)

    @classmethod
    async def create(
        cls,
        source_dir: Directory,
        manifests_dir: Directory,
        projects: list[ProjectSource],
        src_init_paths: list[str],
        workspace_path: str = ".",
        package: list[str] | None = None,
        extra: list[str] | None = None,
        group: list[str] | None = None,
        all_extras: bool = False,
        all_groups: bool = False,
        all_packages: bool = False,
        no_editable: bool = False,
        no_default_groups: bool = False,
        build_backends: list[BuildBackend] | None = None,
    ) -> Self:
        """Parse uv.lock and resolve all build configuration up front.

        Reads only `manifests_dir`; each package's sources come from its project's
        directory in `projects` and are loaded only when the build copies them.
        """
        ws_dir = manifests_dir if workspace_path == "." else manifests_dir.directory(workspace_path)
        packages = [normalize_package_name(p) for p in (package or [])]
        lock_data = tomllib.loads(await ws_dir.file("uv.lock").contents())

        # The workspace root's package name is `uv sync`'s implicit target when no
        # package is requested; resolve it so we mirror that default.
        root_pyproject = await ws_dir.file("pyproject.toml").contents()
        root_name = parse_project_name(root_pyproject)
        default_package = normalize_package_name(root_name) if root_name else None
        # Fail fast on a pure workspace root with no selection (nothing to install).
        require_package_selection(packages, all_packages, default_package)

        groups = selected_groups(root_pyproject, group or [], all_groups, no_default_groups)
        all_local, needed_local = await _discover_local_packages(
            lock_data, packages, all_packages, default_package, groups, workspace_path, manifests_dir
        )
        project_sources = {project.path: project.source for project in projects}
        src_inits = {posixpath.normpath(path) for path in src_init_paths}

        # A local package with no [build-system] or with package = false is deps-only:
        # uv installs its dependencies but never builds the package itself, so its
        # source must not be scaffolded or copied, only its pyproject.toml (for
        # dependency resolution). This holds whether the package is a build target
        # or a transitive workspace dependency (e.g. a Pulumi program whose code
        # lives at the package root, with no src/ or module dir to copy).
        plugins = {await backend.build_backend(): backend for backend in build_backends or []}
        flat_packages: list[str] = []
        local_packages: dict[str, LocalPackage] = {}
        with get_tracer().start_as_current_span("resolve local package layouts") as span:
            span.set_attribute("packages.count", len(all_local))
            for name, pkg_path in all_local.items():
                resolved = posixpath.normpath(posixpath.join(workspace_path, pkg_path))
                contents = await manifests_dir.file(posixpath.join(resolved, "pyproject.toml")).contents()
                metadata = tomllib.loads(contents)
                virtual = (
                    "build-system" not in metadata or metadata.get("tool", {}).get("uv", {}).get("package") is False
                )
                if virtual:
                    flat_packages.append(name)
                plugin = plugins.get(metadata.get("build-system", {}).get("build-backend"))
                layout = await BuildBackendLayout.resolve(plugin, contents, name) if plugin else None
                module = PackageBackend.from_pyproject(metadata, name, layout).module_name or _module_name(name)
                # A src layout has the module's top-level package under `src/`.
                top = module.split(".", 1)[0]
                pkg = LocalPackage(
                    name=name,
                    path=pkg_path,
                    module=module,
                    flat=posixpath.normpath(posixpath.join(resolved, "src", top, "__init__.py")) not in src_inits,
                    pyproject_contents=contents,
                    source=project_sources.get(resolved, source_dir),
                    layout=layout,
                )
                local_packages[name] = pkg
                span.add_event(
                    "package layout",
                    {
                        "package.name": name,
                        "package.path": pkg_path,
                        "package.backend": metadata.get("build-system", {}).get("build-backend", "none"),
                        "package.backend_supported": pkg.build_backend.settings is not None,
                        "package.backend_plugin": layout is not None,
                        "package.virtual": virtual,
                        "package.module_paths": [] if virtual else pkg.module_paths,
                        "package.source_paths": [] if virtual else pkg.source_paths,
                    },
                )

        sync_args = build_uv_sync_args(
            packages=packages,
            extras=extra or [],
            groups=group or [],
            all_extras=all_extras,
            all_groups=all_groups,
            all_packages=all_packages,
            no_editable=no_editable,
            no_default_groups=no_default_groups,
        )

        return cls(
            ws_dir=ws_dir,
            source_dir=source_dir,
            workspace_path=workspace_path,
            all_local=[local_packages[name] for name in all_local],
            needed_local=[local_packages[name] for name in needed_local],
            flat_packages=flat_packages,
            uv_sync_args=sync_args,
            no_editable=no_editable,
        )
