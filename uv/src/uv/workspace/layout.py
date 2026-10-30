from typing import Annotated

from dagger import Doc, field, object_type
from dagger_clients.core import Workspace

from uv.workspace.build_backends.handler import BuildBackendHandler
from uv.workspace.plan import ProjectSource
from uv.workspace.workspace import UvWorkspaceSource


@object_type
class UvProjectLayout:
    """The patterns loading a project's files and those of its local dependencies."""

    path: Annotated[str, Doc("Project root, relative to the workspace root")] = field()
    include: Annotated[list[str], Doc("Glob patterns loading the project's files")] = field()
    exclude: Annotated[list[str], Doc("Glob patterns the project's files leave out")] = field()


@object_type
class UvWorkspaceLayout:
    """The patterns loading a uv workspace's files, as the `pyproject` module discovers them."""

    path: Annotated[str, Doc("Workspace root path (holding uv.lock), relative to the workspace root")] = field()
    include: Annotated[list[str], Doc("Glob patterns loading the members and their path dependencies")] = field()
    exclude: Annotated[list[str], Doc("Glob patterns `include` leaves out")] = field()
    manifest_include: Annotated[list[str], Doc("Glob patterns loading what resolving the workspace reads")] = field()
    manifest_exclude: Annotated[list[str], Doc("Glob patterns `manifest_include` leaves out")] = field()
    projects: Annotated[list[UvProjectLayout], Doc("Each member's and local dependency's patterns")] = field()
    src_paths: Annotated[list[str], Doc("The entries directly under the projects' `src/`")] = field()

    def load(self, ws: Workspace, handlers: list[BuildBackendHandler]) -> UvWorkspaceSource:
        """The workspace's directories, loaded lazily from `ws`."""
        return UvWorkspaceSource(
            path=self.path,
            source=ws.directory("/", include=self.include, exclude=self.exclude, gitignore=True),
            manifests=ws.directory("/", include=self.manifest_include, exclude=self.manifest_exclude),
            projects=[
                ProjectSource(
                    path=project.path,
                    source=ws.directory("/", include=project.include, exclude=project.exclude, gitignore=True),
                )
                for project in self.projects
            ],
            src_paths=self.src_paths,
            build_backend_handlers=handlers,
            ws=ws,
        )
