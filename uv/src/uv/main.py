from typing import Annotated

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
        Directory | None,
        Doc("Directory read instead of the Dagger workspace, when the caller passed one."),
    ] = field(default=None)

    @function
    @get
    def workspace(self, path: WorkspacePath) -> UvWorkspaceSource:
        """The uv workspace rooted at `path`."""
        return UvWorkspaceSource(path=path, source=self.source)


@object_type
class Uv:
    """Entrypoint for the `uv` module.

    Hands out `UvWorkspaceSource` objects — one per workspace — which expose additional functionality.
    Use `workspace` to grab a single workspace by path or `workspaces` to get them all.

    Reads the Dagger workspace it runs in, pulling only the files each operation
    needs. Pass `source` to read a directory instead.

    Learn more in the [docs](https://daggerverse.docs.typesafe.ai/uv/)."""

    source: Annotated[
        Directory | None,
        Doc("Directory to read instead of the Dagger workspace, e.g. a source tree built in a pipeline."),
    ] = field(default=None)
    exclude: Annotated[
        list[str],
        Doc("Glob patterns of workspace paths to leave out of workspaces, e.g. **/tests/_packages/**."),
    ] = field(default=list)

    @function
    def workspace(self, path: WorkspacePath) -> UvWorkspaceSource:
        """A single uv workspace at `path`, relative to the workspace root.

        The returned `UvWorkspaceSource` can `audit` its locked dependencies or
        `build` a minimal container for a package.
        """
        return UvWorkspaceSource(path=path, source=self.source)

    @function
    async def workspaces(self, ws: Workspace) -> UvWorkspaces:
        """Every uv workspace in the source tree (one per uv.lock), less those matching `exclude`.

        Found by walking the workspace on the host, so no files are loaded to
        discover them.
        """
        workspace = self.source.as_workspace() if self.source is not None else ws
        cwd = await workspace.cwd()
        roots = await workspace.find_roots(["uv.lock"], start="/", exclude=[*SOURCE_EXCLUDE, *self.exclude])
        paths = sorted({root_relative(cwd, root) for root in roots})
        return UvWorkspaces(paths=[path for path in paths if not is_excluded(path, self.exclude)], source=self.source)
