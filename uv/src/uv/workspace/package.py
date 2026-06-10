import asyncio
from typing import Annotated

import dagger
from dagger import Doc, field, function, object_type
from dagger.telemetry import get_tracer

from uv.utils import parse_indices
from uv.workspace.index import UvIndex, dicts_to_indices, merge_indices, read_workspace_indices


@object_type
class UvPackageSource:
    """A single member package within a uv workspace.

    Carries the workspace source tree, the workspace root path, and the
    member's own path so it can read both workspace-level and package-level
    index configuration.
    """

    source: Annotated[
        dagger.Directory,
        Doc("Source tree containing the workspace."),
    ] = field()

    workspace_path: Annotated[
        str,
        Doc("Workspace root path within source (`.` for a root workspace)."),
    ] = field()

    package_path: Annotated[
        str,
        Doc("Package path relative to the workspace root."),
    ] = field()

    def _ws_dir(self) -> dagger.Directory:
        return self.source if self.workspace_path == "." else self.source.directory(self.workspace_path)

    def _pkg_dir(self) -> dagger.Directory:
        ws = self._ws_dir()
        return ws if self.package_path == "." else ws.directory(self.package_path)

    @function
    async def indices(
        self,
        include_from_workspace: Annotated[
            bool,
            Doc(
                "Merge indices from the workspace root's configuration. "
                "When True, workspace-level indices are included alongside "
                "the package's own indices (deduplicated by name, package wins)."
            ),
        ] = False,
    ) -> list[UvIndex]:
        """The package indices configured in this member's `pyproject.toml`.

        Reads `[[tool.uv.index]]` from the member's own `pyproject.toml`.
        When `include_from_workspace` is set, also reads workspace-level
        indices and merges them (package-level entries win on name collision).
        """
        tracer = get_tracer()

        async def _read_pkg() -> list[dict]:
            with tracer.start_as_current_span("read package indices") as span:
                span.set_attribute("package.path", self.package_path)
                raw = parse_indices(await self._pkg_dir().file("pyproject.toml").contents())
                span.set_attribute("indices.count", len(raw))
                span.set_attribute("indices.names", [e["name"] for e in raw])
                return raw

        if not include_from_workspace:
            return dicts_to_indices(await _read_pkg())

        async def _read_ws() -> list[dict]:
            with tracer.start_as_current_span("read workspace indices") as span:
                span.set_attribute("workspace.path", self.workspace_path)
                raw = await read_workspace_indices(self._ws_dir())
                span.set_attribute("indices.count", len(raw))
                span.set_attribute("indices.names", [e["name"] for e in raw])
                return raw

        pkg_raw, ws_raw = await asyncio.gather(_read_pkg(), _read_ws())

        with tracer.start_as_current_span("merge indices") as span:
            span.set_attribute("indices.package_count", len(pkg_raw))
            span.set_attribute("indices.workspace_count", len(ws_raw))
            result = merge_indices(ws_raw, pkg_raw)
            span.set_attribute("indices.total", len(result))
            span.set_attribute("indices.names", [r.name for r in result])
            return result
