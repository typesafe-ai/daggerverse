from __future__ import annotations

import posixpath
import re
import tomllib
from pathlib import PurePosixPath
from typing import Annotated

import anyio
import dagger
from dagger import DefaultPath, Doc, Ignore, check, dag, field, function, object_type
from dagger.telemetry import get_tracer
from opentelemetry.trace import Status, StatusCode

_DEFAULT_VERSION = "0.25.1"
_DEFAULT_IMAGE = "ghcr.io/astral-sh/uv"
_DEFAULT_IMAGE_TAG = "0.12.5-python3.13-trixie-slim"


SourceDir = Annotated[
    dagger.Directory,
    Doc("Source directory."),
    DefaultPath("."),
    Ignore(
        [
            "**/__pycache__",
            "**/*.pyc",
            "**/node_modules",
            "**/.venv",
            "**/.tox",
            "**/.nox",
            "**/.git",
            "**/.mypy_cache",
            "**/.pytest_cache",
            "**/.ruff_cache",
            "**/.direnv",
            "**/.devenv",
            "**/dist",
            "**/build",
            "**/*.egg-info",
            "**/sdk",
        ]
    ),
]


def _project_path(pyproject: str) -> str:
    """Source-relative directory holding the given `pyproject.toml`."""
    return posixpath.dirname(pyproject) or "."


def _is_excluded(path: str, patterns: list[str]) -> bool:
    """Whether a project path matches any of the exclude glob patterns."""
    project = PurePosixPath(path)
    return any(project.full_match(pattern) for pattern in patterns)


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
    layout — deptry otherwise infers first-party modules from what sits directly
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


async def _has_package_layout(project_dir: dagger.Directory, module: str | None) -> bool:
    """Whether a project has its own package source — a `src` directory or a
    flat package directory named after the project.

    Used to decide whether deptry should run at all: a `pyproject.toml` with no
    own package (e.g. a uv workspace root that only aggregates members in
    subdirectories) is skipped, so `deptry .` never spills over the rest of the
    monorepo. Projects that do have a package are scanned with `.` as the root,
    letting deptry apply its own defaults and `[tool.deptry]` config (top-level
    modules, `tests`, etc.) rather than being narrowed to `src`.
    """
    entries = {entry.rstrip("/") for entry in await project_dir.entries()}
    return "src" in entries or (module is not None and module in entries)


def _format_failure(exc: dagger.ExecError, project: str) -> str:
    """Human-readable message for a failed deptry exec.

    deptry writes its report to stdout/stderr, but the raised
    :class:`dagger.ExecError` stringifies to only a terse "exit code N".
    Folding the captured output (and the project path) into the message keeps
    the findings in the trace/span error instead of solely in Dagger's logs.
    """
    seen: list[str] = []
    for stream in (exc.stdout, exc.stderr):
        text = (stream or "").strip()
        if text and text not in seen:
            seen.append(text)
    summary = f"deptry found issues in {project} (exit code {exc.exit_code})"
    detail = "\n".join(seen)
    return f"{summary}:\n\n{detail}" if detail else summary


@object_type
class Deptry:
    """Linter for Python dependency issues.

    deptry checks a single Python project: it reads the declared dependencies
    (from a `pyproject.toml`) and scans that project's source for imports to
    find unused, missing, misplaced or transitive dependencies. The `check`
    function runs it once per project across the whole source tree, so a single
    Dagger check covers a monorepo.

    Learn more about deptry at <https://deptry.com>.
    """

    source: SourceDir = field()

    @function
    async def projects(self) -> list[str]:
        """Every checkable Python project in the source tree.

        One entry per `pyproject.toml` that declares dependencies (see
        `_project_info`).
        """
        return [path for path, _, _ in await self._discover()]

    async def _discover(self) -> list[tuple[str, str | None, bool]]:
        """`(path, module_name, configured)` for every checkable project, sorted by path."""
        pyprojects = sorted(await self.source.glob("**/pyproject.toml"))
        discovered: list[tuple[str, str | None, bool]] = []
        for pyproject in pyprojects:
            checkable, module, configured = _project_info(await self.source.file(pyproject).contents())
            if checkable:
                discovered.append((_project_path(pyproject), module, configured))
        return discovered

    def _container(self, version: str) -> dagger.Container:
        return (
            dag.container()
            .from_(f"{_DEFAULT_IMAGE}:{_DEFAULT_IMAGE_TAG}")
            .with_env_variable("UV_COMPILE_BYTECODE", "1")
            # put the `deptry` launcher on PATH so a plain `deptry` works.
            .with_env_variable("UV_TOOL_BIN_DIR", "/usr/local/bin")
            .with_exec(["uv", "tool", "install", f"deptry=={version}"])
        )

    @check
    @function
    async def check(
        self,
        version: Annotated[
            str,
            Doc("deptry version to use (ignored when `ctr` is set)."),
        ] = _DEFAULT_VERSION,
        ctr: Annotated[
            dagger.Container | None,
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
        exclude: Annotated[
            list[str] | None,
            Doc("Glob patterns (source-relative) of project paths to skip, e.g. `**/tests/_packages/**`."),
        ] = None,
    ) -> None:
        """Run `deptry` for every Python project in the source tree, in parallel.

        Discovers projects by scanning for `pyproject.toml` files that declare
        dependencies and runs `deptry .` in each. A project with no package of
        its own — e.g. a uv workspace root that only aggregates members in
        subdirectories — is skipped (so the scan never spills over the rest of
        the monorepo) unless it declares a `[tool.deptry]` section, which opts it
        in explicitly. Exits non-zero when any (non-excluded) project reports
        dependency issues.
        """
        patterns = exclude or []
        tracer = get_tracer()

        with tracer.start_as_current_span("discover Python projects"):
            projects = [t for t in await self._discover() if not _is_excluded(t[0], patterns)]

        with tracer.start_as_current_span("build deptry container"):
            container = ctr if ctr is not None else self._container(version)

        failed: list[str] = []
        checked: list[str] = []

        async def _run(project: str, module: str | None, configured: bool) -> None:
            # Each deptry run is wrapped in its own OpenTelemetry span so it
            # shows up as a distinct node in the trace, and failures are captured
            # per-project so one failure can't cancel the sibling runs — every
            # project is always checked to completion.
            with tracer.start_as_current_span(f"deptry: {project}") as span:
                # Run when the project has a recognized layout, or when it
                # declares `[tool.deptry]` config (an explicit opt-in that also
                # covers non-standard layouts). Otherwise skip, so an aggregator
                # like a uv workspace root doesn't scan the whole monorepo.
                if not configured and not await _has_package_layout(self.source.directory(project), module):
                    span.set_status(Status(StatusCode.OK, "skipped: no src/flat layout and no [tool.deptry] config"))
                    return
                checked.append(project)
                try:
                    known = ["--known-first-party", module] if module else []
                    await (
                        container.with_workdir("/work")
                        .with_directory("/work", self.source.directory(project))
                        .with_exec(["deptry", "--no-ansi", *known, *(args or []), "."])
                        .sync()
                    )
                except dagger.ExecError as exc:
                    message = _format_failure(exc, project)
                    span.set_status(Status(StatusCode.ERROR, message))
                    span.record_exception(exc)
                    failed.append(project)
                except Exception as exc:  # noqa: BLE001
                    span.set_status(Status(StatusCode.ERROR, str(exc)))
                    span.record_exception(exc)
                    failed.append(project)

        async with anyio.create_task_group() as tg:
            for project, module, configured in projects:
                tg.start_soon(_run, project, module, configured)

        if failed:
            msg = f"deptry found issues in {len(failed)} of {len(checked)} project(s): {', '.join(sorted(failed))}"
            raise RuntimeError(msg)
