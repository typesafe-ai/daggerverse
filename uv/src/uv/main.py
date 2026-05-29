import posixpath
from dataclasses import dataclass
from typing import Annotated

import anyio
import dagger
from dagger import Doc, check, field, function, object_type
from dagger.telemetry import get_tracer
from opentelemetry.trace import Status, StatusCode

from uv.args import SourceDir
from uv.utils import (
    image_ref,
    is_excluded,
    pyproject_path,
    uv_toml_path,
    workspace_path,
)
from uv.workspace import UvWorkspaceSource


@dataclass(frozen=True)
class LocatedWorkspace:
    """A UvWorkspaceSource paired with its source-relative path.

    Local-only glue (not a Dagger type): keeps UvWorkspaceSource self-contained
    while letting the audit check filter, report, and trace workspaces by path.
    """

    path: str
    workspace: UvWorkspaceSource


async def _resolve_image(workspace: UvWorkspaceSource, uv_version: str | None, image: str | None) -> str:
    """Pick the uv image: explicit `image` > `image_ref(uv_version)` > detected."""
    if image is not None:
        return image
    return image_ref(uv_version or await workspace.uv_version())


@object_type
class Uv:
    """Tooling for uv-managed Python projects."""

    source: SourceDir = field()

    def _workspace(self, lockfile: str, *, has_uv_toml: bool) -> UvWorkspaceSource:
        return UvWorkspaceSource(
            uv_lock=self.source.file(lockfile),
            pyproject=self.source.file(pyproject_path(lockfile)),
            uv_toml=(self.source.file(uv_toml_path(lockfile)) if has_uv_toml else None),
        )

    async def _source_workspaces(self) -> list[LocatedWorkspace]:
        """Discover every uv workspace in the source tree (one per uv.lock)."""
        lockfiles = sorted(await self.source.glob("**/uv.lock"))
        uv_toml_dirs = {posixpath.dirname(p) for p in await self.source.glob("**/uv.toml")}
        return [
            LocatedWorkspace(
                path=workspace_path(lockfile),
                workspace=self._workspace(
                    lockfile,
                    has_uv_toml=posixpath.dirname(lockfile) in uv_toml_dirs,
                ),
            )
            for lockfile in lockfiles
        ]

    @function
    async def workspaces(self) -> list[UvWorkspaceSource]:
        """Every uv workspace in the source tree (one per uv.lock)."""
        return [sw.workspace for sw in await self._source_workspaces()]

    @function
    async def audit_workspace(
        self,
        uv_lock: Annotated[dagger.File, Doc("The workspace's uv.lock file.")],
        pyproject: Annotated[dagger.File, Doc("The workspace's pyproject.toml file.")],
        uv_toml: Annotated[
            dagger.File | None,
            Doc("The workspace's uv.toml configuration file, if present."),
        ] = None,
        uv_version: Annotated[
            str | None,
            Doc(
                "uv version (image tag) to run with. Defaults to the version "
                "detected from the workspace; ignored when `image` is set."
            ),
        ] = None,
        image: Annotated[
            str | None,
            Doc("Full uv image reference to run with. Overrides `uv_version`."),
        ] = None,
    ) -> None:
        """Run ``uv audit`` for a single workspace."""
        workspace = UvWorkspaceSource(uv_lock=uv_lock, pyproject=pyproject, uv_toml=uv_toml)
        resolved = await _resolve_image(workspace, uv_version, image)
        await workspace.audit(resolved).run()

    @check
    @function
    async def audit(
        self,
        exclude: Annotated[
            list[str] | None,
            Doc("Glob patterns (source-relative) of workspace paths to skip, e.g. `**/tests/_packages/**`."),
        ] = None,
        uv_version: Annotated[
            str | None,
            Doc(
                "uv version (image tag) to run with. Defaults to the version "
                "detected per workspace; ignored when `image` is set."
            ),
        ] = None,
        image: Annotated[
            str | None,
            Doc("Full uv image reference to run with. Overrides `uv_version`."),
        ] = None,
    ) -> None:
        """Run ``uv audit`` for every workspace in parallel.

        Exits non-zero when any (non-excluded) workspace fails its audit.
        """
        patterns = exclude or []
        workspaces = [ws for ws in await self._source_workspaces() if not is_excluded(ws.path, patterns)]

        tracer = get_tracer()
        failed: list[str] = []

        async def _run(ws: LocatedWorkspace) -> None:
            # audit_workspace runs in-process (not over the Dagger API), so it
            # gets no span of its own. Wrap it in a custom OpenTelemetry span so
            # each workspace audit shows up as its own node in the trace.
            #
            # Capture failures per-workspace so one error can't cancel the
            # sibling audits — every workspace is always audited to completion.
            # The failing uv audit exec surfaces its output in the Dagger UI.
            with tracer.start_as_current_span(f"audit({ws.path})") as span:
                try:
                    await self.audit_workspace(
                        ws.workspace.uv_lock,
                        ws.workspace.pyproject,
                        ws.workspace.uv_toml,
                        uv_version=uv_version,
                        image=image,
                    )
                except Exception as exc:  # noqa: BLE001
                    span.set_status(Status(StatusCode.ERROR))
                    span.record_exception(exc)
                    failed.append(ws.path)

        async with anyio.create_task_group() as tg:
            for ws in workspaces:
                tg.start_soon(_run, ws)

        if failed:
            msg = f"uv audit failed for {len(failed)} of {len(workspaces)} workspace(s): {', '.join(sorted(failed))}"
            raise RuntimeError(msg)
