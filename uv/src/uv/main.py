"""Build, audit, and package uv-managed Python projects and workspaces."""

from typing import Annotated

import anyio
import dagger
from dagger import Doc, check, field, function, object_type
from dagger.telemetry import get_tracer
from opentelemetry.trace import Status, StatusCode

from uv.args import SourceDir, WorkspacePath
from uv.utils import (
    format_audit_failure,
    is_excluded,
    workspace_path,
)
from uv.workspace import UvWorkspaceSource


@object_type
class Uv:
    """Entrypoint for the `uv` module.

    Hands out `UvWorkspaceSource` objects — one per workspace — which expose additional functionality.
    Use `workspace` to grab a single workspace by path or `get_workspaces` to list them all.

    Learn more in the [docs](https://daggerverse.docs.typesafe.ai/uv/)."""

    source: SourceDir = field()

    @function
    def workspace(self, path: WorkspacePath = ".") -> UvWorkspaceSource:
        """A single uv workspace at `path` within the source tree.

        The returned `UvWorkspaceSource` can `audit` its locked dependencies or
        `build` a minimal container for a package.
        """
        return UvWorkspaceSource(source=self.source, path=path)

    @function
    async def get_workspaces(self) -> list[UvWorkspaceSource]:
        """Every uv workspace in the source tree (one per uv.lock)."""
        lockfiles = sorted(await self.source.glob("**/uv.lock"))
        return [UvWorkspaceSource(source=self.source, path=workspace_path(lockfile)) for lockfile in lockfiles]

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
        workspaces = [ws for ws in await self.get_workspaces() if not is_excluded(ws.path, patterns)]

        tracer = get_tracer()
        failed: list[str] = []

        async def _run(ws: UvWorkspaceSource) -> None:
            # The audit runs in-process (not over the Dagger API), so it gets no
            # span of its own. Wrap it in a custom OpenTelemetry span so each
            # workspace audit shows up as its own node in the trace.
            #
            # Capture failures per-workspace so one error can't cancel the
            # sibling audits — every workspace is always audited to completion.
            with tracer.start_as_current_span(f"audit({ws.path})") as span:
                try:
                    await (await ws.audit(uv_version=uv_version, image=image)).run()
                # We swallow the exception here (so a failure can't cancel the
                # sibling audits), so the span's own record_exception /
                # set_status_on_exception never fire — and even if they did they
                # would use str(exc), which for an ExecError is just
                # "exit code N". So set the status explicitly: uv writes its
                # vulnerability report to stdout/stderr, and folding that (plus
                # this workspace's path) into the span status is what makes the
                # findings show up as this node's error in the trace rather than
                # only in Dagger's stderr logs. The raised error below stays
                # terse — the full report lives on each workspace's span.
                except dagger.ExecError as exc:
                    message = format_audit_failure(exc.exit_code, exc.stdout, exc.stderr, ws.path)
                    span.set_status(Status(StatusCode.ERROR, message))
                    span.record_exception(exc)
                    failed.append(ws.path)
                except Exception as exc:  # noqa: BLE001
                    span.set_status(Status(StatusCode.ERROR, str(exc)))
                    span.record_exception(exc)
                    failed.append(ws.path)

        async with anyio.create_task_group() as tg:
            for ws in workspaces:
                tg.start_soon(_run, ws)

        if failed:
            msg = f"uv audit failed for {len(failed)} of {len(workspaces)} workspace(s): {', '.join(sorted(failed))}"
            raise RuntimeError(msg)
