import tempfile
from pathlib import Path
from typing import Annotated, Self

from dagger import Doc, collection, field, function, get, keys, object_type
from dagger_clients.core import Directory, File, Workspace

from pyproject import discovery
from pyproject.discovery import DEFAULT_WORKSPACE_MARKERS, SOURCE_EXCLUDE, join, matches, tree

ProjectPath = Annotated[
    str, Doc("Project root (holding pyproject.toml), relative to the workspace root. `.` for the root.")
]
WorkspacePath = Annotated[str, Doc("Workspace root (holding a workspace marker), relative to the workspace root.")]


def _load(ws: Workspace, include: list[str], exclude: list[str]) -> Directory:
    """`include` less `exclude` and gitignored files, loaded lazily from `ws`."""
    return ws.directory("/", include=include, exclude=exclude, gitignore=True)


@object_type
class PyprojectProject:
    """A Python project: a directory holding a `pyproject.toml`.

    Every file belongs to the deepest project containing it, so a project's own
    files stop at the projects nested inside it (`nested`).
    """

    path: Annotated[str, Doc("Project root, relative to the workspace root. `.` for the root.")] = field()
    name: Annotated[str | None, Doc("`[project].name`, or null for a project without one.")] = field()
    generated: Annotated[
        bool,
        Doc(
            "Whether Dagger generated this project (`[tool.dagger] generated`), such as a module's `sdk/` or `clients/*`."
        ),
    ] = field()
    workspace: Annotated[
        str | None,
        Doc("Root of the workspace that installs this project as a member, or null when none does."),
    ] = field()
    nested: Annotated[
        list[str],
        Doc("Roots of the topmost projects nested inside this one, whose files are their own."),
    ] = field()
    local_dependencies: Annotated[
        list[str],
        Doc(
            "Roots of the projects this one depends on through `[tool.uv.sources]` "
            "(path and workspace sources, transitively), wherever they live."
        ),
    ] = field()
    include: Annotated[
        list[str],
        Doc("Glob patterns of what `source` loads: the project and its local dependencies."),
    ] = field()
    exclude: Annotated[
        list[str],
        Doc("Glob patterns `source` leaves out: caches, virtualenvs, build output and unrelated nested projects."),
    ] = field()
    files_include: Annotated[list[str], Doc("Glob patterns of what `files` loads: the project's tree.")] = field()
    files_exclude: Annotated[
        list[str],
        Doc("Glob patterns `files` leaves out: caches, virtualenvs, build output and the projects nested inside."),
    ] = field()

    # Not `field()`s: what the functions return, loaded lazily from the workspace `pyproject()` was given.
    pyproject_file: File
    files_dir: Directory
    source_dir: Directory

    @classmethod
    def of(cls, project: discovery.Project, ws: Workspace) -> Self:
        manifest = join(project.path, "pyproject.toml")
        files_include = tree(project.path)
        files_exclude = [*SOURCE_EXCLUDE, *(pattern for nested in project.nested for pattern in tree(nested))]
        return cls(
            path=project.path,
            name=project.name,
            generated=project.generated,
            workspace=project.workspace,
            nested=list(project.nested),
            local_dependencies=list(project.local_dependencies),
            include=list(project.include),
            exclude=list(project.exclude),
            files_include=files_include,
            files_exclude=files_exclude,
            pyproject_file=_load(ws, [manifest], []).file(manifest),
            files_dir=_load(ws, files_include, files_exclude),
            source_dir=_load(ws, list(project.include), list(project.exclude)),
        )

    @function
    def pyproject(self) -> File:
        """This project's `pyproject.toml`."""
        return self.pyproject_file

    @function
    def files(self) -> Directory:
        """The files this project owns, at their workspace-root-relative paths.

        Its tree less the projects nested in it, caches, virtualenvs, build
        output and gitignored files. This is what a tool that works on one
        project's own code, such as a linter, should see.
        """
        return self.files_dir

    @function
    def source(self) -> Directory:
        """What building or installing this project reads, at its workspace-root-relative paths.

        The project's own files and those of its local dependencies, so a path
        source like `../lib` resolves when the directory is mounted as the
        workspace root. Projects nested inside those trees that none of them
        needs are left out, as are caches, virtualenvs, build output and
        gitignored files.
        """
        return self.source_dir


@object_type
@collection
class PyprojectProjects:
    """Python projects, keyed by root."""

    paths: Annotated[list[str], Doc("Project roots, relative to the workspace root.")] = keys()
    items: list[PyprojectProject]

    @function
    @get
    def project(self, path: ProjectPath) -> PyprojectProject:
        """The project rooted at `path`."""
        for item in self.items:
            if item.path == path:
                return item
        msg = f"no project at {path!r}"
        raise ValueError(msg)


@object_type
class PyprojectWorkspace:
    """A workspace: a directory holding a workspace marker, such as a lockfile.

    It is the environment root that installs its members and their local
    dependencies. A uv workspace's members are the projects its
    `[tool.uv.workspace]` selects; any other workspace has its root project
    alone, or none for a bare lockfile.
    """

    path: Annotated[str, Doc("Workspace root, relative to the workspace root. `.` for the root.")] = field()
    lockfiles: Annotated[list[str], Doc("Workspace markers found at the root, e.g. `uv.lock`.")] = field()
    members: Annotated[list[str], Doc("Roots of the member projects, the root project first.")] = field()
    local_dependencies: Annotated[
        list[str],
        Doc("Roots of the projects the members depend on through path sources, outside the membership."),
    ] = field()
    member_items: list[PyprojectProject]
    include: Annotated[
        list[str],
        Doc("Glob patterns of what `source` loads: every member and their local dependencies."),
    ] = field()
    exclude: Annotated[
        list[str],
        Doc("Glob patterns `source` leaves out: caches, virtualenvs, build output and unrelated nested projects."),
    ] = field()
    manifest_include: Annotated[
        list[str],
        Doc("Glob patterns of what `manifests` loads: lockfiles, `uv.toml`, `pyproject.toml` and `.python-version`."),
    ] = field()
    manifest_exclude: Annotated[list[str], Doc("Glob patterns `manifests` leaves out.")] = field()

    # Not `field()`s: what the functions return, loaded lazily from the workspace `pyproject()` was given.
    source_dir: Directory
    manifests_dir: Directory

    @function
    def projects(self) -> PyprojectProjects:
        """This workspace's member projects."""
        return PyprojectProjects(paths=self.members, items=self.member_items)

    @function
    def source(self) -> Directory:
        """What building or installing this workspace reads, at its workspace-root-relative paths.

        Every member's files and those of their local dependencies, wherever
        they live. Projects nested inside those trees that none of them needs
        are left out, as are caches, virtualenvs, build output and gitignored
        files.
        """
        return self.source_dir

    @function
    def manifests(self) -> Directory:
        """What resolving this workspace's dependencies reads, and nothing else.

        The markers (lockfiles), `uv.toml`, and every member's and local
        dependency's `pyproject.toml` and `.python-version`, at their
        workspace-root-relative paths. A source edit changes none of them, so
        a dependency layer built from this stays cached across source edits.
        """
        return self.manifests_dir


@object_type
@collection
class PyprojectWorkspaces:
    """Python workspaces, keyed by root."""

    paths: Annotated[list[str], Doc("Workspace roots, relative to the workspace root.")] = keys()
    items: list[PyprojectWorkspace]

    @function
    @get
    def workspace(self, path: WorkspacePath) -> PyprojectWorkspace:
        """The workspace rooted at `path`."""
        for item in self.items:
            if item.path == path:
                return item
        msg = f"no workspace at {path!r}"
        raise ValueError(msg)


@object_type
class Pyproject:
    """Discovers the Python projects and workspaces in a Dagger workspace.

    A project is a directory holding a `pyproject.toml`; a workspace is a
    directory holding a workspace marker (`uv.lock` or `pylock.toml` by
    default). Each comes with the scoped source it needs, so tools built on
    this load one project's files instead of the whole repository.

    Learn more in the [docs](https://daggerverse.docs.typesafe.ai/pyproject/)."""

    project_items: list[PyprojectProject]
    workspace_items: list[PyprojectWorkspace]

    @classmethod
    async def create(
        cls,
        ws: Workspace,
        exclude: Annotated[
            list[str] | None,
            Doc(
                "Glob patterns of project and workspace roots to leave out, e.g. `**/tests/_packages/**`. "
                "Excluded projects still bound the projects around them and still count as dependencies."
            ),
        ] = None,
        workspace_markers: Annotated[
            list[str],
            Doc("File names that make a directory a workspace, e.g. `pylock.prod.toml` for a named PEP 751 lock."),
        ] = DEFAULT_WORKSPACE_MARKERS,
    ) -> Self:
        patterns = exclude or []

        # Discovery reads only the manifests and markers: load them, less gitignored
        # files, in one call and walk them locally instead of one engine call per file.
        names = ["pyproject.toml", *workspace_markers]
        local = Path(tempfile.mkdtemp())
        await _load(ws, [f"**/{name}" for name in names], SOURCE_EXCLUDE).export(str(local))
        texts: dict[str, str] = {}
        lockfiles: dict[str, list[str]] = {}
        for name in names:
            for file in local.glob(f"**/{name}"):
                path = file.parent.relative_to(local).as_posix()
                if name == "pyproject.toml":
                    texts[path] = file.read_text()
                else:
                    lockfiles.setdefault(path, []).append(name)

        found = discovery.Discovery(texts, lockfiles)
        projects = {}

        def project(path: str) -> PyprojectProject:
            if path not in projects:
                projects[path] = PyprojectProject.of(found.project(path), ws)
            return projects[path]

        workspaces = []
        for path in found.workspace_paths:
            if matches(path, patterns):
                continue
            workspace = found.workspace(path)
            workspaces.append(
                PyprojectWorkspace(
                    path=workspace.path,
                    lockfiles=list(workspace.lockfiles),
                    members=list(workspace.members),
                    local_dependencies=list(workspace.local_dependencies),
                    member_items=[project(member) for member in workspace.members],
                    include=list(workspace.include),
                    exclude=list(workspace.exclude),
                    manifest_include=list(workspace.manifest_include),
                    manifest_exclude=list(SOURCE_EXCLUDE),
                    source_dir=_load(ws, list(workspace.include), list(workspace.exclude)),
                    manifests_dir=_load(ws, list(workspace.manifest_include), list(SOURCE_EXCLUDE)),
                )
            )
        return cls(
            project_items=[project(path) for path in found.project_paths if not matches(path, patterns)],
            workspace_items=workspaces,
        )

    @function
    def projects(self) -> PyprojectProjects:
        """Every Python project in the workspace, less those matching `exclude`."""
        return PyprojectProjects(paths=[p.path for p in self.project_items], items=self.project_items)

    @function
    def workspaces(self) -> PyprojectWorkspaces:
        """Every workspace in the workspace, less those matching `exclude`."""
        return PyprojectWorkspaces(paths=[w.path for w in self.workspace_items], items=self.workspace_items)

    @function
    def project_list(self) -> list[PyprojectProject]:
        """The projects `projects` holds, in one call.

        Listing a collection gets each item with its own call; use this to read
        every project at once.
        """
        return self.project_items

    @function
    def workspace_list(self) -> list[PyprojectWorkspace]:
        """The workspaces `workspaces` holds, in one call.

        Listing a collection gets each item with its own call; use this to read
        every workspace at once.
        """
        return self.workspace_items
