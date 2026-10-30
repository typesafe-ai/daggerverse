from __future__ import annotations

import asyncio
import posixpath
import re
import tomllib
from pathlib import Path
from typing import Annotated

import dagger
from dagger import Doc, check, collection, field, function, get, keys, object_type
from dagger_clients.core import Container, Directory, Workspace, core
from dagger_clients.pyproject import pyproject

_DEFAULT_VERSION = "0.25.1"


def pinned_image(name: str) -> str:
    """The image reference in this package's `images/<name>/Dockerfile`, which Dependabot keeps current."""
    dockerfile = Path(__file__).resolve().parent / "images" / name / "Dockerfile"
    for line in dockerfile.read_text().splitlines():
        if line.startswith("FROM "):
            return line.split()[1]
    msg = f"no FROM line in {dockerfile}"
    raise ValueError(msg)


# The uv image deptry is installed into.
_BASE_IMAGE = pinned_image("uv")


def _root_path(path: str, *names: str) -> str:
    return "/" + posixpath.normpath(posixpath.join(path, *names))


def _to_module_name(name: str) -> str:
    """Import module name for a distribution name.

    Lowercases and collapses runs of `-`/`_`/`.` to a single `_`, matching how
    `uv_build` derives a package's import name from `[project].name` (so a
    `Foo-Bar` distribution maps to the `foo_bar` module).
    """
    return re.sub(r"[-_.]+", "_", name.lower())


def _project_info(pyproject_content: str) -> tuple[bool, str | None, bool]:
    """`(checkable, module_name, configured)` for a `pyproject.toml`.

    Only PEP 621 `[project]` metadata (as used by uv) is read. A project is
    *checkable* when it declares `[project].dependencies`; a file without it
    (e.g. tooling-only config) is skipped rather than run and hard-failed.

    `module_name` is the project's first-party import name, derived from
    `[project].name`. It is passed to deptry as `--known-first-party` so the
    project's own modules aren't misreported as third-party regardless of
    layout. Otherwise deptry infers first-party modules from what sits directly
    under the scanned root, which misses a `src` layout (false DEP001/DEP003).

    `configured` is whether the project declares a `[tool.deptry]` section: an
    explicit signal that deptry is meant to run here, so the project is checked
    even without a recognized `src`/flat layout.
    """
    try:
        data = tomllib.loads(pyproject_content)
    except tomllib.TOMLDecodeError:
        return False, None, False
    project = data.get("project", {})
    if "dependencies" not in project:
        return False, None, False
    name = project.get("name")
    module = _to_module_name(name) if isinstance(name, str) else None
    configured = "deptry" in data.get("tool", {})
    return True, module, configured


async def _has_package_layout(workspace: Workspace, path: str, module: str | None) -> bool:
    """Whether a project has its own package source: a `src` directory or a
    flat package directory named after the project.

    Used to decide whether deptry should run at all: a `pyproject.toml` with no
    own package (e.g. a uv workspace root that only aggregates members in
    subdirectories) is skipped, so `deptry .` never spills over the rest of the
    monorepo. Projects that do have a package are scanned with `.` as the root,
    letting deptry apply its own defaults and `[tool.deptry]` config (top-level
    modules, `tests`, etc.) rather than being narrowed to `src`.
    """
    names = ["src", module] if module is not None else ["src"]
    for name in names:
        if await workspace.glob(_root_path(path, name).lstrip("/")):
            return True
    return False


# SGR (color) escape sequences. deptry colors its report on the live exec
# stream (which Dagger renders as a terminal), but the same bytes become `[1m`
# noise when folded into the plain-text error message, so strip them there, not
# at the source.
_ANSI_SGR_RE = re.compile(r"\x1b\[[0-9;]*m")


def _format_failure(exc: dagger.ExecError, project: str) -> str:
    """Human-readable message for a failed deptry exec.

    deptry writes its report to stdout/stderr, but the raised
    :class:`dagger.ExecError` stringifies to only a terse "exit code N".
    Folding the captured output (and the project path) into the message keeps
    the findings in the check's error instead of solely in Dagger's logs.
    Color codes are stripped here since the error is rendered as plain text
    (the live exec output keeps its color).
    """
    seen: list[str] = []
    for stream in (exc.stdout, exc.stderr):
        text = _ANSI_SGR_RE.sub("", stream or "").strip()
        if text and text not in seen:
            seen.append(text)
    summary = f"deptry found issues in {project} (exit code {exc.exit_code})"
    detail = "\n".join(seen)
    return f"{summary}:\n\n{detail}" if detail else summary


@object_type
class DeptryProject:
    """One Python project that deptry checks."""

    path: Annotated[
        str,
        Doc("Project path, relative to the workspace root. `.` for the root."),
    ] = field()

    module: str | None
    files: Directory

    @check
    @function
    async def check(
        self,
        version: Annotated[
            str,
            Doc("deptry version to use (ignored when `ctr` is set)."),
        ] = _DEFAULT_VERSION,
        ctr: Annotated[
            Container | None,
            Doc(
                "Container with deptry installed. Defaults to the official "
                "ghcr.io/astral-sh/uv image with deptry installed as a uv tool. "
                "Overrides `version`."
            ),
        ] = None,
        args: Annotated[
            list[str] | None,
            Doc("Additional arguments to pass to deptry (added before the project root)."),
        ] = None,
    ) -> None:
        """Run `deptry .` in this project and fail on any dependency issue.

        Only the files this project owns are loaded: its directory less the
        projects nested in it, such as generated Dagger code or test fixtures.
        """
        container = ctr if ctr is not None else _container(version)
        known = ["--known-first-party", self.module] if self.module else []
        project_dir = self.files if self.path == "." else self.files.directory(self.path)
        try:
            await (
                # `FORCE_COLOR` makes deptry colorize its report even though its
                # stdout is a pipe, so Dagger's rendered exec logs show color. The
                # copy folded into the raised error has the codes stripped (see
                # `_format_failure`). Pass `--no-ansi` via `args` to opt out.
                container.with_env_variable("FORCE_COLOR", "1")
                .with_workdir("/work")
                .with_directory("/work", project_dir)
                .with_exec(["deptry", *known, *(args or []), "."])
                .sync()
            )
        except dagger.ExecError as exc:
            raise RuntimeError(_format_failure(exc, self.path)) from exc


@object_type
@collection
class DeptryProjects:
    """Every Python project deptry checks in the source tree, keyed by its path.

    `check` runs once per selected project, so each project is its own check
    that can be selected or skipped on its own.
    """

    paths: Annotated[
        list[str],
        Doc("Project paths, relative to the workspace root. `.` for the root."),
    ] = keys()

    items: list[DeptryProject]

    @function
    @get
    def project(self, path: str) -> DeptryProject:
        """The project at `path`."""
        for item in self.items:
            if item.path == path:
                return item
        msg = f"deptry checks no project at {path!r}"
        raise ValueError(msg)


def _container(version: str) -> Container:
    return (
        core()
        .container()
        .from_(_BASE_IMAGE)
        .with_env_variable("UV_COMPILE_BYTECODE", "1")
        # put the `deptry` launcher on PATH so a plain `deptry` works.
        .with_env_variable("UV_TOOL_BIN_DIR", "/usr/local/bin")
        .with_exec(["uv", "tool", "install", f"deptry=={version}"])
    )


@object_type
class Deptry:
    """Linter for Python dependency issues.

    deptry checks a single Python project: it reads the declared dependencies
    (from a `pyproject.toml`) and scans that project's source for imports to
    find unused, missing, misplaced or transitive dependencies. `projects`
    discovers every project in the source tree, and its `check` runs once per
    project, so one Dagger check covers a monorepo.

    Learn more about deptry at <https://deptry.com>.
    """

    items: list[DeptryProject]

    @classmethod
    async def create(
        cls,
        ws: Workspace,
        exclude: Annotated[
            list[str] | None,
            Doc("Glob patterns of project paths to leave out of projects, e.g. **/tests/_packages/**."),
        ] = None,
    ) -> Deptry:
        """Discover the projects: one per `pyproject.toml` the `pyproject` module
        finds, other than Dagger-generated ones, that declares dependencies (see `_project_info`) and either has its
        own package source or declares a `[tool.deptry]` section. A project with
        no package of its own (e.g. a uv workspace root that only aggregates
        members in subdirectories) is left out, unless its `[tool.deptry]`
        section opts it in explicitly. Paths matching `exclude` are left out too.

        The workspace is walked on the host, and only the pyproject.toml files
        are loaded.
        """
        found = await pyproject(ws, exclude=exclude).projects().list_()

        async def _project(project) -> DeptryProject | None:
            # Dagger's generated sdk/ and clients/ are projects too, but not ours to check.
            if await project.generated():
                return None
            path = await project.path()
            checkable, module, configured = _project_info(await project.pyproject().contents())
            if checkable and (configured or await _has_package_layout(ws, path, module)):
                return DeptryProject(path=path, module=module, files=project.files())
            return None

        projects = await asyncio.gather(*(_project(project) for project in found))
        return cls(items=[project for project in projects if project is not None])

    @function
    def projects(self) -> DeptryProjects:
        """Every Python project deptry checks in the source tree."""
        return DeptryProjects(paths=[item.path for item in self.items], items=self.items)
