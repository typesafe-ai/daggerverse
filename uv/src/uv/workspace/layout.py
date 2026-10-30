from collections.abc import Callable
from dataclasses import dataclass

from dagger_clients.core import Directory, Workspace

from uv.workspace.build_backends.handler import BuildBackendHandler
from uv.workspace.plan import ProjectSource, UvSyncPlan
from uv.workspace.workspace import UvWorkspaceSource

# Loads the files matching `include` less `exclude`.
_Loader = Callable[[list[str], list[str]], Directory]


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

    def _projects(self, load: _Loader) -> list[ProjectSource]:
        return [
            ProjectSource(path=project.path, source=load(project.include, project.exclude)) for project in self.projects
        ]

    async def inputs(self, ws: Workspace, handlers: list[BuildBackendHandler]) -> Directory:
        """Every file the workspace's builds read, loaded lazily from `ws`.

        The manifests, members and their path dependencies, and the handlers'
        `/`-prefixed build inputs, which may lie outside every member (e.g. a
        Cargo workspace above them).
        """

        def load(include: list[str], exclude: list[str]) -> Directory:
            return ws.directory("/", include=include, exclude=exclude, gitignore=True)

        source = load(self.include, self.exclude)
        manifests = ws.directory("/", include=self.manifest_include, exclude=self.manifest_exclude)
        plan = await UvSyncPlan.create(
            source_dir=source,
            manifests_dir=manifests,
            projects=self._projects(load),
            src_paths=self.src_paths,
            workspace_path=self.path,
            all_packages=True,
            handlers=handlers,
        )
        inputs = manifests.with_directory(".", source)
        if plan.root_inputs:
            inputs = inputs.with_directory(".", load(plan.root_inputs, []))
        return inputs

    def load(self, inputs: Directory, handlers: list[BuildBackendHandler]) -> UvWorkspaceSource:
        """The workspace, its directories filtered lazily from `inputs`."""

        def load(include: list[str], exclude: list[str]) -> Directory:
            return inputs.filter(include=include, exclude=exclude)

        return UvWorkspaceSource(
            path=self.path,
            source=load(self.include, self.exclude),
            manifests=load(self.manifest_include, self.manifest_exclude),
            projects=self._projects(load),
            src_paths=self.src_paths,
            build_backend_handlers=handlers,
            inputs_dir=inputs,
        )
