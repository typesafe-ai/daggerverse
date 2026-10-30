import asyncio
import dataclasses
import posixpath
from typing import Annotated, Self

from dagger import Doc, collection, function, get, keys, object_type
from dagger_clients.core import Workspace
from dagger_clients.pyproject import pyproject

from uv.args import WorkspacePath
from uv.workspace import UvWorkspaceSource
from uv.workspace.build_backends.handler import BuildBackendHandler, BuildBackendHandlers
from uv.workspace.layout import UvProjectLayout, UvWorkspaceLayout

# A uv workspace is a directory holding a uv.lock.
_WORKSPACE_MARKERS = ["uv.lock"]


async def _load(
    ws: Workspace | None, layouts: list[UvWorkspaceLayout], handlers: list[BuildBackendHandler], path: str
) -> UvWorkspaceSource:
    """The workspace at `path`, loaded from a workspace of just the files its builds read.

    The workspace `uv()` was given gets a new cache key on every run, and so
    does everything loaded from it. One made from a directory is keyed by its
    contents, so loading the workspace from it keeps its builds cached.
    """
    assert ws is not None
    layout = next((layout for layout in layouts if layout.path == path), None)
    if layout is None:
        msg = f"no uv workspace (uv.lock) at {path!r}"
        raise ValueError(msg)
    inputs = await layout.load(ws, handlers).inputs()
    return layout.load(inputs.as_workspace(), handlers)


def _project_of_src_path(path: str) -> str:
    """The project root of a `<root>/src/<entry>` path, `.` for the workspace root."""
    return posixpath.dirname(posixpath.dirname(path)) or "."


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

    # Not `field()`s: each workspace loads only when `workspace` gets it.
    ws: Workspace | None = None
    layouts: list[UvWorkspaceLayout] = dataclasses.field(default_factory=list)
    handlers: list[BuildBackendHandler] = dataclasses.field(default_factory=list)

    @function
    @get
    async def workspace(self, path: WorkspacePath) -> UvWorkspaceSource:
        """The uv workspace rooted at `path`."""
        return await _load(self.ws, self.layouts, self.handlers, path)


@object_type
class Uv:
    """Entrypoint for the `uv` module.

    Hands out one `UvWorkspaceSource` per uv workspace, as the `pyproject`
    module discovers them.
    Use `workspace` to grab a single workspace by path or `workspaces` to get them all,
    `build_backend_handlers` for handlers that configure the built-in backends, and
    `with_build_backend_handlers` to apply them.

    Each workspace it hands out is loaded from just the files its builds read,
    as a workspace of its own. Dagger caches that by its contents, so the
    workspace's builds rerun only when one of those files changes.

    Learn more in the [docs](https://daggerverse.docs.typesafe.ai/uv/)."""

    # Not `field()`s: Dagger can't expose a `Workspace` as one, and the rest is internal.
    ws: Workspace | None = None
    layouts: list[UvWorkspaceLayout] = dataclasses.field(default_factory=list)
    handlers: list[BuildBackendHandler] = dataclasses.field(default_factory=list)

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
        found, projects = await asyncio.gather(discovered.workspace_list(), discovered.project_list())

        # Keep only the patterns `pyproject` computed: `workspace` loads each
        # workspace from them, so no call goes back to the `pyproject` module.
        async def project_layout(project) -> UvProjectLayout:
            path, include, exclude = await asyncio.gather(project.path(), project.include(), project.exclude())
            return UvProjectLayout(path=path, include=include, exclude=exclude)

        async def workspace_layout(workspace) -> UvWorkspaceLayout:
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
            return UvWorkspaceLayout(
                path=path,
                include=include,
                exclude=exclude,
                manifest_include=manifest_include,
                manifest_exclude=manifest_exclude,
                projects=[layouts[root] for root in roots if root in layouts],
                # `<root>/src/<module>` marks a project's src layout, including namespace and stub packages.
                src_paths=[path for path in src_paths if _project_of_src_path(path) in roots],
            )

        # One glob for every workspace: each `Workspace.glob` walks the whole tree.
        src_paths = [posixpath.normpath(path) for path in await ws.glob("**/src/*")]
        layouts = {layout.path: layout for layout in await asyncio.gather(*(project_layout(p) for p in projects))}
        return cls(ws=ws, layouts=list(await asyncio.gather(*(workspace_layout(w) for w in found))))

    @function
    def build_backend_handlers(self) -> BuildBackendHandlers:
        """Handlers for the module's built-in build backends, to configure them for some packages.

        Pass them to `with_build_backend_handlers`, e.g. a maturin handler with
        the crates a package's Cargo workspace keeps above it.
        """
        return BuildBackendHandlers()

    @function
    def with_build_backend_handlers(
        self,
        handlers: Annotated[
            list[BuildBackendHandler],
            Doc(
                "Handlers for the local packages' build backends: from `build_backend_handlers()`, or another "
                "module's `BuildBackendHandler`. They take precedence over the module's own support for their "
                "backends."
            ),
        ],
    ) -> Self:
        """These workspaces with `handlers` added to their build backend handlers.

        Every workspace `workspace` and `workspaces` hand out loads the files
        they read, and its `builder` uses them. When several handle the same
        backend, the last one added configures its packages.
        """
        return dataclasses.replace(self, handlers=[*self.handlers, *handlers])

    @function
    async def workspace(self, path: WorkspacePath) -> UvWorkspaceSource:
        """A single uv workspace at `path`, relative to the workspace root.

        The returned `UvWorkspaceSource` can `audit` its locked dependencies or
        get a `builder` for its packages.
        """
        return await _load(self.ws, self.layouts, self.handlers, path)

    @function
    def workspaces(self) -> UvWorkspaces:
        """Every uv workspace in the source tree (one per uv.lock), less those matching `exclude`.

        Each loads only when `workspace` gets it.
        """
        return UvWorkspaces(
            paths=[layout.path for layout in self.layouts], ws=self.ws, layouts=self.layouts, handlers=self.handlers
        )
