from __future__ import annotations

import posixpath
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
    """Import module name for a distribution name (hyphens/dots -> underscores)."""
    return name.replace("-", "_").replace(".", "_")


def _project_info(pyproject_content: str) -> tuple[bool, str | None]:
    """`(checkable, module_name)` for a `pyproject.toml`.

    Only PEP 621 `[project]` metadata (as used by uv) is read. A project is
    *checkable* when it declares `[project].dependencies`; a file without it
    (e.g. tooling-only config) is skipped rather than run and hard-failed.

    `module_name` is the project's first-party import name, derived from
    `[project].name`. It is passed to deptry as `--known-first-party` so the
    project's own modules aren't misreported as third-party regardless of
    layout — deptry otherwise infers first-party modules from what sits directly
    under the scanned root, which misses a `src` layout (false DEP001/DEP003).
    """
    try:
        data = tomllib.loads(pyproject_content)
    except tomllib.TOMLDecodeError:
        return False, None
    project = data.get("project", {})
    if "dependencies" not in project:
        return False, None
    name = project.get("name")
    module = _to_module_name(name) if isinstance(name, str) else None
    return True, module


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
        return [path for path, _ in await self._discover()]

    async def _discover(self) -> list[tuple[str, str | None]]:
        """`(path, module_name)` for every checkable project, sorted by path."""
        pyprojects = sorted(await self.source.glob("**/pyproject.toml"))
        discovered: list[tuple[str, str | None]] = []
        for pyproject in pyprojects:
            checkable, module = _project_info(await self.source.file(pyproject).contents())
            if checkable:
                discovered.append((_project_path(pyproject), module))
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
        dependencies and runs `deptry .` in each. Exits non-zero when any
        (non-excluded) project reports dependency issues.
        """
        patterns = exclude or []
        tracer = get_tracer()

        with tracer.start_as_current_span("discover Python projects"):
            projects = [(p, m) for p, m in await self._discover() if not _is_excluded(p, patterns)]

        with tracer.start_as_current_span("build deptry container"):
            container = ctr if ctr is not None else self._container(version)

        failed: list[str] = []

        async def _run(project: str, module: str | None) -> None:
            # Each deptry run is wrapped in its own OpenTelemetry span so it
            # shows up as a distinct node in the trace, and failures are captured
            # per-project so one failure can't cancel the sibling runs — every
            # project is always checked to completion.
            with tracer.start_as_current_span(f"deptry: {project}") as span:
                try:
                    known = ["--known-first-party", module] if module else []
                    await (
                        container.with_workdir("/work")
                        .with_directory("/work", self.source.directory(project))
                        .with_exec(["deptry", *known, *(args or []), "."])
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
            for project, module in projects:
                tg.start_soon(_run, project, module)

        if failed:
            msg = f"deptry found issues in {len(failed)} of {len(projects)} project(s): {', '.join(sorted(failed))}"
            raise RuntimeError(msg)
