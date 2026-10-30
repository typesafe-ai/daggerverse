from dataclasses import dataclass

from dagger_clients.core import Workspace

from uv.workspace.build_backends.handler import BuildBackendHandler
from uv.workspace.plan import ProjectSource
from uv.workspace.workspace import UvWorkspaceSource


@dataclass
class UvProjectLayout:
    """The patterns loading a project's files and those of its local dependencies. Internal state of `Uv`."""

    path: str
    include: list[str]
    exclude: list[str]


@dataclass
class UvWorkspaceLayout:
    """The patterns loading a uv workspace's files, as `pyproject` discovers them. Internal state of `Uv`."""

    path: str
    include: list[str]
    exclude: list[str]
    manifest_include: list[str]
    manifest_exclude: list[str]
    projects: list[UvProjectLayout]
    src_paths: list[str]

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
