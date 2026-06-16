import asyncio
import posixpath
import tomllib
from pathlib import PurePosixPath
from typing import Annotated

import dagger
from dagger import Doc, dag, field, function, object_type
from dagger.telemetry import get_tracer

from uv.args import (
    AllExtras,
    AllGroups,
    AllPackages,
    DaggerCodegen,
    Extra,
    Group,
    NoEditable,
    Package,
)
from uv.utils import (
    _DEFAULT_VERSION,
    debian_image_ref,
    extract_indices,
    image_ref,
    parse_indices,
    parse_required_version_from_pyproject,
    parse_required_version_from_uv_toml,
    resolve_specifier,
)
from uv.workspace.audit import Audit
from uv.workspace.build import UvWorkspaceBuild
from uv.workspace.index import UvIndex, dicts_to_indices, merge_indices
from uv.workspace.package import UvPackageSource
from uv.workspace.plan import UvSyncPlan
from uv.workspace.venv import UvVenv


@object_type
class UvWorkspaceSource:
    """A self-contained uv workspace: the source files rooted at a `uv.lock`.

    Carries the source tree and the workspace's path within it (`"."` for a
    root workspace). From those it derives everything it needs to `audit` the
    locked dependencies or `build` a minimal container for a package — keeping
    the parent `Uv` a thin, container-free entrypoint.

    The full `source` (rather than just the sliced workspace directory) is
    carried so builds can reach sibling path-dependencies that live *outside*
    the workspace root (e.g. `../my-dep` in a nested workspace).

    Named `UvWorkspaceSource` (not `UvWorkspace`) to avoid colliding with
    the top-level `Uv` constructor when composed in a parent module.
    """

    source: Annotated[
        dagger.Directory,
        Doc("Source tree containing the workspace (and any sibling path-dependencies)."),
    ] = field()

    path: Annotated[
        str,
        Doc("Workspace root path (holding uv.lock/pyproject.toml) within `source`. `.` for a root workspace."),
    ] = field(default=".")

    def _ws_dir(self) -> dagger.Directory:
        """The workspace directory (`uv.lock`/`pyproject.toml` live here)."""
        return self.source if self.path == "." else self.source.directory(self.path)

    async def _uv_toml(self) -> dagger.File | None:
        """The workspace's `uv.toml`, if one sits alongside its `uv.lock`."""
        ws = self._ws_dir()
        if "uv.toml" in await ws.entries():
            return ws.file("uv.toml")
        return None

    async def _required_version(self) -> str | None:
        """The `required-version` specifier declared by the workspace, if any.

        Prefers `uv.toml` (top-level) over `[tool.uv]` in `pyproject.toml`.
        """
        uv_toml = await self._uv_toml()
        if uv_toml is not None:
            value = parse_required_version_from_uv_toml(await uv_toml.contents())
            if value is not None:
                return value
        pyproject = self._ws_dir().file("pyproject.toml")
        return parse_required_version_from_pyproject(await pyproject.contents())

    @function
    async def uv_version(self) -> str:
        """The uv version this workspace requires, as a concrete image tag.

        Reads `required-version` (a PEP 440 specifier) from `uv.toml` or the
        `[tool.uv]` table of `pyproject.toml`. Exact pins are used as-is; ranges
        resolve to their minimal compatible version (no PyPI lookup). Falls back
        to the default `latest` tag when unspecified.
        """
        specifier = await self._required_version()
        if specifier is None:
            return _DEFAULT_VERSION
        return resolve_specifier(specifier)

    @function
    async def python_version(self) -> str | None:
        """The Python version pinned by the workspace's `.python-version`, if any.

        Returns its contents (e.g. `3.13.7`), or `None` when the file is absent.
        `build` stages this pin so `uv venv`/`uv sync` select the exact interpreter
        instead of resolving `requires-python`.
        """
        ws = self._ws_dir()
        if ".python-version" not in await ws.entries():
            return None
        return (await ws.file(".python-version").contents()).strip()

    @staticmethod
    def _member_paths_from(pyproject_data: dict, all_pyprojects: list[str]) -> list[str]:
        """Workspace member paths from pre-parsed pyproject.toml and a glob result."""
        members_globs = pyproject_data.get("tool", {}).get("uv", {}).get("workspace", {}).get("members", [])
        if not members_globs:
            return []
        return sorted(
            d
            for p in all_pyprojects
            if (d := posixpath.dirname(p) or ".") != "." and any(PurePosixPath(d).full_match(g) for g in members_globs)
        )

    @function
    async def indices(
        self,
        include_from_members: Annotated[
            bool,
            Doc(
                "Merge indices from all workspace members' pyproject.toml files. "
                "When True, member-level indices are included alongside "
                "workspace-level indices (member entries win on name collision)."
            ),
        ] = False,
    ) -> list[UvIndex]:
        """The package indices configured for this workspace.

        Reads `[[index]]` from `uv.toml` when present, otherwise falls back
        to `[[tool.uv.index]]` in `pyproject.toml` — matching uv's own
        precedence (`uv.toml` overrides the entire `[tool.uv]` section).

        When `include_from_members` is set, also reads `[[tool.uv.index]]`
        from every workspace member's `pyproject.toml` and merges them
        (member entries take precedence on name collision).
        """
        tracer = get_tracer()
        ws = self._ws_dir()

        uv_toml = await self._uv_toml()
        pyproject_data = tomllib.loads(await ws.file("pyproject.toml").contents())

        with tracer.start_as_current_span("read workspace indices") as ws_span:
            ws_span.set_attribute("workspace.path", self.path)
            if uv_toml is not None:
                ws_raw = parse_indices(await uv_toml.contents(), uv_toml=True)
            else:
                ws_raw = extract_indices(pyproject_data)
            ws_span.set_attribute("indices.count", len(ws_raw))
            ws_span.set_attribute("indices.names", [e["name"] for e in ws_raw])

        if not include_from_members:
            return dicts_to_indices(ws_raw)

        member_paths = self._member_paths_from(pyproject_data, await ws.glob("**/pyproject.toml"))

        async def _read_member(member_path: str) -> list[dict]:
            with tracer.start_as_current_span(f"read member indices ({member_path})") as m_span:
                content = await ws.directory(member_path).file("pyproject.toml").contents()
                raw = parse_indices(content)
                m_span.set_attribute("member.path", member_path)
                m_span.set_attribute("indices.count", len(raw))
                m_span.set_attribute("indices.names", [e["name"] for e in raw])
                return raw

        member_results = await asyncio.gather(*[_read_member(p) for p in member_paths])
        member_raw = [e for r in member_results for e in r]

        with tracer.start_as_current_span("merge indices") as merge_span:
            merge_span.set_attribute("indices.workspace_count", len(ws_raw))
            merge_span.set_attribute("indices.member_count", len(member_raw))
            result = merge_indices(ws_raw, member_raw)
            merge_span.set_attribute("indices.total", len(result))
            merge_span.set_attribute("indices.names", [r.name for r in result])
            return result

    @function
    def package(
        self,
        path: Annotated[
            str,
            Doc("Member package path relative to the workspace root."),
        ],
    ) -> UvPackageSource:
        """A single member package within this workspace."""
        return UvPackageSource(
            source=self.source,
            workspace_path=self.path,
            package_path=path,
        )

    @function
    async def audit(
        self,
        uv_version: Annotated[
            str | None,
            Doc(
                "uv version (image tag) to audit with. Defaults to the version "
                "detected from the workspace; ignored when `image` is set."
            ),
        ] = None,
        image: Annotated[
            str | None,
            Doc("Full uv image reference to audit with. Overrides `uv_version`."),
        ] = None,
    ) -> Audit:
        """Audit this workspace's locked dependencies.

        The image is resolved here (explicit `image` > `image_ref(uv_version)` >
        the version detected from the workspace) and handed to a container-bound
        `Audit`; resolving an image *tag* is pure string work, so this type stays
        container-free.
        """
        resolved = image if image is not None else image_ref(uv_version or await self.uv_version())
        ws = self._ws_dir()
        return Audit(
            uv_lock=ws.file("uv.lock"),
            pyproject=ws.file("pyproject.toml"),
            image=resolved,
            uv_toml=await self._uv_toml(),
        )

    @staticmethod
    async def _has_uv(ctr: dagger.Container) -> bool:
        """Check whether uv is on $PATH in the given container."""
        with get_tracer().start_as_current_span("detect uv on PATH") as span:
            exit_code = await ctr.with_exec(["sh", "-c", "command -v uv"], expect=dagger.ReturnType.ANY).exit_code()
            found = exit_code == 0
            span.set_attribute("uv.found", found)
            return found

    async def _default_base_container(self) -> dagger.Container:
        """A Debian-based uv image pinned to this workspace's uv version.

        Uses the Debian variant rather than the distroless `uv:<version>` image
        because the latter is `FROM scratch`: `uv sync` can't provision a managed
        Python there (no libc). On Debian, uv downloads Python on first sync and
        ca-certificates are already present. Pass an explicit `base_container` to
        build on your own auth/system-package layers.
        """
        return dag.container().from_(debian_image_ref(await self.uv_version())).with_workdir("/work")

    @function
    async def build(
        self,
        base_container: Annotated[
            dagger.Container | None,
            Doc(
                "Container to build on top of (auth, system packages, etc.). Defaults to "
                "a Debian-based uv image at the workspace's uv version (uv provisions Python on demand)."
            ),
        ] = None,
        package: Package | None = None,
        extra: Extra | None = None,
        group: Group | None = None,
        all_extras: AllExtras = False,
        all_groups: AllGroups = False,
        all_packages: AllPackages = False,
        dagger_codegen: DaggerCodegen = True,
        no_editable: NoEditable = False,
        auto_install_uv: Annotated[
            bool,
            Doc(
                "Automatically install the uv binary if it is not already on $PATH. "
                "Set to False if your base_container already ships uv."
            ),
        ] = True,
    ) -> UvWorkspaceBuild:
        """Prepare a build for this workspace without installing anything yet.

        Resolves the sync plan, mounts the uv cache, and copies the root
        pyproject.toml and uv.lock into the container. Nothing is installed —
        the returned `UvWorkspaceBuild` drives the pipeline from here:
        `with_remote_dependencies()` to install remote deps, `with_workspace_files()`
        to scaffold local packages, then `with_local_dependencies()` to install them.
        Skip `with_remote_dependencies()` when another tool (e.g. `pulumi install`)
        handles dependency installation.
        """
        with get_tracer().start_as_current_span("prepare workspace build") as span:
            span.set_attribute("workspace.path", self.path)
            span.set_attribute("build.packages", package or [])
            span.set_attribute("build.all_packages", all_packages)
            span.set_attribute("build.default_base_container", base_container is None)

            plan = await UvSyncPlan.create(
                source_dir=self.source,
                workspace_path=self.path,
                package=package,
                extra=extra,
                group=group,
                all_extras=all_extras,
                all_groups=all_groups,
                all_packages=all_packages,
                dagger_codegen=dagger_codegen,
                no_editable=no_editable,
            )
            span.set_attribute("uv.sync_args", plan.uv_sync_args)
            span.set_attribute("build.local_packages", [pkg.name for pkg in plan.needed_local])

            ctr = base_container if base_container is not None else await self._default_base_container()
            workdir = await ctr.workdir()

            ctr = ctr.with_mounted_cache("/root/.cache/uv", dag.cache_volume("uv-cache"))

            # Place workspace files at their real path within the source tree
            # so that relative paths in uv.lock resolve correctly.
            ws_ctr_path = posixpath.join(workdir, self.path) if self.path != "." else workdir
            ctr = ctr.with_file(
                posixpath.join(ws_ctr_path, "pyproject.toml"),
                plan.ws_dir.file("pyproject.toml"),
            ).with_file(posixpath.join(ws_ctr_path, "uv.lock"), plan.ws_dir.file("uv.lock"))

            python_version = await self.python_version()
            if python_version:
                ctr = ctr.with_new_file(posixpath.join(ws_ctr_path, ".python-version"), python_version)

            ctr = ctr.with_workdir(ws_ctr_path)
            ctr = await ctr.sync()

            build = UvWorkspaceBuild(container=ctr, plan=plan)

            if auto_install_uv and not await self._has_uv(ctr):
                build = await build.with_uv(await self.uv_version())

            return build

    @function
    async def install(
        self,
        base_container: Annotated[
            dagger.Container | None,
            Doc(
                "Container to build on (auth, system packages, etc.). Defaults to "
                "a Debian-based uv image at the workspace's uv version (uv provisions Python on demand)."
            ),
        ] = None,
        package: Package | None = None,
        extra: Extra | None = None,
        group: Group | None = None,
        all_extras: AllExtras = False,
        all_groups: AllGroups = False,
        all_packages: AllPackages = False,
        dagger_codegen: DaggerCodegen = True,
        no_editable: NoEditable = False,
        venv: Annotated[
            bool,
            Doc("Create the virtual environment up front with `uv venv` (before installing)."),
        ] = False,
        venv_relocatable: Annotated[
            bool,
            Doc("When `venv` is set, make it relocatable (`uv venv --relocatable`)."),
        ] = False,
        auto_install_uv: Annotated[
            bool,
            Doc(
                "Automatically install the uv binary if it is not already on $PATH. "
                "Set to False if your base_container already ships uv."
            ),
        ] = True,
    ) -> dagger.Container:
        """Build a minimal container with deps installed for the given package(s).

        Convenience method composing `build`, optionally `with_venv`,
        `with_remote_dependencies`, `with_workspace_files`, and
        `with_local_dependencies`. For fine-grained control (e.g. running
        `pulumi install` between remote deps and local source), call them individually.
        """
        b = await self.build(
            base_container,
            package=package,
            extra=extra,
            group=group,
            all_extras=all_extras,
            all_groups=all_groups,
            all_packages=all_packages,
            dagger_codegen=dagger_codegen,
            no_editable=no_editable,
            auto_install_uv=auto_install_uv,
        )
        if venv:
            b = await b.with_venv(relocatable=venv_relocatable)
        b = await b.with_remote_dependencies()
        b = await b.with_workspace_files()
        return await b.with_local_dependencies()

    @function
    async def venv(
        self,
        base_container: Annotated[
            dagger.Container | None,
            Doc("Container to build on; defaults to a Debian-based uv image at the workspace's uv version."),
        ] = None,
        package: Package | None = None,
        extra: Extra | None = None,
        group: Group | None = None,
        all_extras: AllExtras = False,
        all_groups: AllGroups = False,
        all_packages: AllPackages = False,
        dagger_codegen: DaggerCodegen = True,
        no_editable: NoEditable = False,
        auto_install_uv: Annotated[
            bool,
            Doc(
                "Automatically install the uv binary if it is not already on $PATH. "
                "Set to False if your base_container already ships uv."
            ),
        ] = True,
    ) -> UvVenv:
        """Install into a relocatable venv and export it with the Python it links against.

        Like `install`, but always builds a relocatable venv and returns a `UvVenv`
        (the venv plus its uv-managed Python) that `.into(container, path)` can drop
        into any image. Use this — rather than `install` — when copying the
        environment into another container. Pair with `no_editable=True` so the
        exported venv carries no dependency on the workspace source.
        """
        b = await self.build(
            base_container,
            package=package,
            extra=extra,
            group=group,
            all_extras=all_extras,
            all_groups=all_groups,
            all_packages=all_packages,
            dagger_codegen=dagger_codegen,
            no_editable=no_editable,
            auto_install_uv=auto_install_uv,
        )
        b = await b.with_venv(relocatable=True)
        b = await b.with_remote_dependencies()
        b = await b.with_workspace_files()
        b = b.with_container(await b.with_local_dependencies())
        return await b.venv()
