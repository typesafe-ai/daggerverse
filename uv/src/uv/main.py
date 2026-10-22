from typing import Annotated, Self

from dagger import Doc, collection, field, function, get, keys, object_type
from dagger_clients.core import Directory, Workspace

from uv.args import SOURCE_EXCLUDE, WorkspacePath
from uv.utils import is_excluded, root_relative
from uv.workspace import UvWorkspaceSource


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

    source: Annotated[
        Directory,
        Doc("Source tree containing the workspaces."),
    ] = field()

    @function
    @get
    def workspace(self, path: WorkspacePath) -> UvWorkspaceSource:
        """The uv workspace rooted at `path`."""
        return UvWorkspaceSource(source=self.source, path=path)


@object_type
class Uv:
    """Entrypoint for the `uv` module.

    Hands out one `UvWorkspaceSource` per uv workspace.
    Use `workspace` to grab a single workspace by path or `workspaces` to get them all.

    Learn more in the [docs](https://daggerverse.docs.typesafe.ai/uv/)."""

    source: Annotated[
        Directory,
        Doc("Source tree: the Dagger workspace, less caches, virtualenvs and gitignored files."),
    ] = field()

    workspace_paths: Annotated[
        list[str],
        Doc("Paths of the uv workspaces found in the source tree, relative to its root."),
    ] = field()

    @classmethod
    async def create(
        cls,
        ws: Workspace,
        exclude: Annotated[
            list[str] | None,
            Doc("Glob patterns of workspace paths to leave out of workspaces, e.g. **/tests/_packages/**."),
        ] = None,
    ) -> Self:
        # Discovery walks the workspace on the host, so it loads no files.
        patterns = exclude or []
        cwd = await ws.cwd()
        roots = await ws.find_roots(["uv.lock"], start="/", exclude=[*SOURCE_EXCLUDE, *patterns])
        paths = sorted({root_relative(cwd, root) for root in roots})
        return cls(
            source=ws.directory("/", exclude=SOURCE_EXCLUDE, gitignore=True),
            workspace_paths=[path for path in paths if not is_excluded(path, patterns)],
        )

    @function
    def workspace(self, path: WorkspacePath) -> UvWorkspaceSource:
        """A single uv workspace at `path`, relative to the workspace root.

        The returned `UvWorkspaceSource` can `audit` its locked dependencies or
        `build` a minimal container for a package.
        """
        return UvWorkspaceSource(source=self.source, path=path)

    @function
    def workspaces(self) -> UvWorkspaces:
        """Every uv workspace in the source tree (one per uv.lock), less those matching `exclude`."""
        return UvWorkspaces(paths=self.workspace_paths, source=self.source)
