import dataclasses
from collections.abc import Callable
from dataclasses import dataclass

from dagger_clients.core import CacheVolume, Container, Directory, Workspace

from uv.audit import CooldownPolicy, UpgradeStrategy
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
    upgrade_strategy: UpgradeStrategy = UpgradeStrategy.LATEST
    audit_cooldown: CooldownPolicy = CooldownPolicy.RESPECT
    audit_concurrency: int = 10
    audit_exclude: list[str] = dataclasses.field(default_factory=list)
    audit_base_container: Container | None = None
    uv_cache: CacheVolume | None = None
    # The local packages' `/`-prefixed build inputs, once `with_root_inputs` resolved them.
    root_inputs: list[str] | None = None

    def _projects(self, load: _Loader) -> list[ProjectSource]:
        return [
            ProjectSource(path=project.path, source=load(project.include, project.exclude)) for project in self.projects
        ]

    @staticmethod
    def _loader(ws: Workspace) -> _Loader:
        def load(include: list[str], exclude: list[str]) -> Directory:
            return ws.directory("/", include=include, exclude=exclude, gitignore=True)

        return load

    def _manifests(self, ws: Workspace) -> Directory:
        return ws.directory("/", include=self.manifest_include, exclude=self.manifest_exclude)

    async def with_root_inputs(self, ws: Workspace, handlers: list[BuildBackendHandler]) -> "UvWorkspaceLayout":
        """This layout with its root inputs resolved: the handlers' `/`-prefixed build
        inputs, which may lie outside every member (e.g. a Cargo workspace above them).

        Reads the manifests, so `inputs` itself makes no call.
        """
        if self.root_inputs is not None:
            return self
        load = self._loader(ws)
        plan = await UvSyncPlan.create(
            source_dir=load(self.include, self.exclude),
            manifests_dir=self._manifests(ws),
            projects=self._projects(load),
            src_paths=self.src_paths,
            workspace_path=self.path,
            all_packages=True,
            handlers=handlers,
        )
        return dataclasses.replace(self, root_inputs=plan.root_inputs)

    def inputs(self, ws: Workspace) -> Directory:
        """Every file the workspace's builds read, loaded lazily from `ws`.

        The manifests, members and their path dependencies, and the root inputs
        `with_root_inputs` resolved.
        """
        assert self.root_inputs is not None
        load = self._loader(ws)
        inputs = self._manifests(ws).with_directory(".", load(self.include, self.exclude))
        if self.root_inputs:
            inputs = inputs.with_directory(".", load(self.root_inputs, []))
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
            upgrade_strategy=self.upgrade_strategy,
            audit_cooldown=self.audit_cooldown,
            audit_concurrency=self.audit_concurrency,
            audit_exclude=self.audit_exclude,
            audit_base_container=self.audit_base_container,
            uv_cache=self.uv_cache,
        )
