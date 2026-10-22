from typing import Annotated

from dagger import Doc, collection, field, function, get, keys, object_type
from dagger_clients.core import Directory

from uv.args import SourceDir, WorkspacePath
from uv.utils import is_excluded, workspace_path
from uv.workspace import UvWorkspaceSource


@object_type
@collection
class UvWorkspaces:
    """Every uv workspace in the source tree (one per uv.lock), keyed by its path.

    `audit` runs once per selected workspace, so each workspace is its own check
    that can be selected or skipped on its own.
    """

    source: Annotated[
        Directory,
        Doc("Source tree containing the workspaces."),
    ] = field()

    paths: Annotated[
        list[str],
        Doc("Workspace root paths (holding uv.lock) within `source`. `.` for a root workspace."),
    ] = keys()

    @function
    @get
    def workspace(self, path: WorkspacePath) -> UvWorkspaceSource:
        """The uv workspace rooted at `path`."""
        return UvWorkspaceSource(source=self.source, path=path)


@object_type
class Uv:
    """Entrypoint for the `uv` module.

    Hands out `UvWorkspaceSource` objects — one per workspace — which expose additional functionality.
    Use `workspace` to grab a single workspace by path or `workspaces` to get them all.

    Learn more in the [docs](https://daggerverse.docs.typesafe.ai/uv/)."""

    source: SourceDir = field()
    exclude: Annotated[
        list[str],
        Doc(
            "Glob patterns (source-relative) of workspace paths to leave out of workspaces, e.g. **/tests/_packages/**."
        ),
    ] = field(default=list)

    @function
    def workspace(self, path: WorkspacePath) -> UvWorkspaceSource:
        """A single uv workspace at `path` within the source tree.

        The returned `UvWorkspaceSource` can `audit` its locked dependencies or
        `build` a minimal container for a package.
        """
        return UvWorkspaceSource(source=self.source, path=path)

    @function
    async def workspaces(self) -> UvWorkspaces:
        """Every uv workspace in the source tree (one per uv.lock), less those matching `exclude`."""
        paths = [workspace_path(lockfile) for lockfile in sorted(await self.source.glob("**/uv.lock"))]
        return UvWorkspaces(source=self.source, paths=[path for path in paths if not is_excluded(path, self.exclude)])
