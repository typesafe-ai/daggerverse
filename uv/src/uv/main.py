import asyncio
import posixpath
from typing import Annotated, Self

from dagger import Doc, collection, function, get, keys, object_type
from dagger_clients.core import Workspace
from dagger_clients.pyproject import pyproject

from uv.args import WorkspacePath
from uv.workspace import UvWorkspaceSource
from uv.workspace.build_backends.handler import BuildBackendHandlers
from uv.workspace.plan import ProjectSource

# A uv workspace is a directory holding a uv.lock.
_WORKSPACE_MARKERS = ["uv.lock"]


def _lookup(items: list[UvWorkspaceSource], path: str) -> UvWorkspaceSource:
    for item in items:
        if item.path == path:
            return item
    msg = f"no uv workspace (uv.lock) at {path!r}"
    raise ValueError(msg)


def _project_of_src_init(path: str) -> str:
    """The project root of a `<root>/src/<module>/__init__.py` path, `.` for the workspace root."""
    return posixpath.dirname(posixpath.dirname(posixpath.dirname(path))) or "."


@object_type
@collection
class UvWorkspaces:
    """Every uv workspace in the source tree (one per uv.lock), keyed by its path.

    `audit` runs once per selected workspace, so each workspace is its own check
    that can be selected or skipped on its own.
    """

    paths: Annotated[
        list[str],
        Doc("uv workspace root paths (holding uv.lock), relative to the workspace root. `.` for the root."),
    ] = keys()

    items: list[UvWorkspaceSource]

    @function
    @get
    def workspace(self, path: WorkspacePath) -> UvWorkspaceSource:
        """The uv workspace rooted at `path`."""
        return _lookup(self.items, path)


@object_type
class Uv:
    """Entrypoint for the `uv` module.

    Hands out one `UvWorkspaceSource` per uv workspace, as the `pyproject`
    module discovers them.
    Use `workspace` to grab a single workspace by path or `workspaces` to get them all,
    and `build_backend_handlers` for handlers that configure the built-in backends.

    Learn more in the [docs](https://daggerverse.docs.typesafe.ai/uv/)."""

    items: list[UvWorkspaceSource]

    @classmethod
    async def create(
        cls,
        ws: Workspace,
        exclude: Annotated[
            list[str] | None,
            Doc("Glob patterns of workspace paths to leave out of workspaces, e.g. **/tests/_packages/**."),
        ] = None,
    ) -> Self:
        discovered = pyproject(ws, exclude=exclude, workspace_markers=_WORKSPACE_MARKERS)
        found, projects = await asyncio.gather(discovered.workspaces().list_(), discovered.projects().list_())

        # Read the patterns `pyproject` computed and build the directories here: each
        # `Workspace.directory` is lazy, so nothing loads until a build reads it, and
        # no call goes back to the `pyproject` module per workspace or project.
        async def project_source(project) -> ProjectSource:
            path, include, exclude = await asyncio.gather(project.path(), project.include(), project.exclude())
            return ProjectSource(path=path, source=ws.directory("/", include=include, exclude=exclude, gitignore=True))

        async def workspace_source(workspace) -> UvWorkspaceSource:
            path, members, dependencies, include, exclude, manifest_include, manifest_exclude = await asyncio.gather(
                workspace.path(),
                workspace.members(),
                workspace.local_dependencies(),
                workspace.include(),
                workspace.exclude(),
                workspace.manifest_include(),
                workspace.manifest_exclude(),
            )
            roots = [*members, *dependencies]
            return UvWorkspaceSource(
                path=path,
                source=ws.directory("/", include=include, exclude=exclude, gitignore=True),
                manifests=ws.directory("/", include=manifest_include, exclude=manifest_exclude),
                projects=[sources[root] for root in roots if root in sources],
                # `<root>/src/<module>/__init__.py` marks a project's src layout.
                src_init_paths=[init for init in src_inits if _project_of_src_init(init) in roots],
            )

        # One glob for every workspace: each `Workspace.glob` walks the whole tree.
        src_inits = await ws.glob("**/src/*/__init__.py")
        sources = {source.path: source for source in await asyncio.gather(*(project_source(p) for p in projects))}
        return cls(items=list(await asyncio.gather(*(workspace_source(w) for w in found))))

    @function
    def build_backend_handlers(self) -> BuildBackendHandlers:
        """Handlers for the module's built-in build backends, to configure them for some packages.

        Pass them as `build_backend_handlers` to `build`, `install` or `venv`, e.g. a
        maturin handler with the crates a package's Cargo workspace keeps above it.
        """
        return BuildBackendHandlers()

    @function
    def workspace(self, path: WorkspacePath) -> UvWorkspaceSource:
        """A single uv workspace at `path`, relative to the workspace root.

        The returned `UvWorkspaceSource` can `audit` its locked dependencies or
        `build` a minimal container for a package.
        """
        return _lookup(self.items, path)

    @function
    def workspaces(self) -> UvWorkspaces:
        """Every uv workspace in the source tree (one per uv.lock), less those matching `exclude`."""
        return UvWorkspaces(paths=[item.path for item in self.items], items=self.items)
