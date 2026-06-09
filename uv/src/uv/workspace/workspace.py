import posixpath
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
    image_ref,
    parse_indices,
    parse_required_version_from_pyproject,
    parse_required_version_from_uv_toml,
    resolve_specifier,
)
from uv.workspace.audit import Audit
from uv.workspace.build import UvWorkspaceBuild
from uv.workspace.index import UvIndex
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

    @function
    async def indices(self) -> list[UvIndex]:
        """The package indices configured for this workspace.

        Reads `[[index]]` from `uv.toml` when present, otherwise falls back
        to `[[tool.uv.index]]` in `pyproject.toml` — matching uv's own
        precedence (`uv.toml` overrides the entire `[tool.uv]` section).
        """
        uv_toml = await self._uv_toml()
        if uv_toml is not None:
            raw = parse_indices(await uv_toml.contents(), uv_toml=True)
        else:
            raw = parse_indices(await self._ws_dir().file("pyproject.toml").contents())
        return [UvIndex(name=entry["name"], url=entry["url"], publish_url=entry["publish_url"]) for entry in raw]

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
        # Wrap the prep in a span so the trace shows what this phase resolved
        # (the requested selection, the chosen base, and the computed plan)
        # distinctly from the later install steps. No deps are installed here —
        # this only resolves the plan (uv.lock parse, codegen, local-package
        # discovery) and stages the base image + pyproject.toml/uv.lock.
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

            ctr = ctr.with_file(
                posixpath.join(workdir, "pyproject.toml"),
                plan.ws_dir.file("pyproject.toml"),
            ).with_file(posixpath.join(workdir, "uv.lock"), plan.ws_dir.file("uv.lock"))

            # Stage the workspace's Python pin so `uv venv`/`uv sync` select the
            # locked interpreter instead of falling back to `requires-python`.
            python_version = await self.python_version()
            if python_version:
                ctr = ctr.with_new_file(posixpath.join(workdir, ".python-version"), python_version)

            # `with_file`/`with_mounted_cache` are lazy; sync() inside the span so it
            # captures the base pull + file staging rather than just plan resolution.
            ctr = await ctr.sync()

            return UvWorkspaceBuild(container=ctr, plan=plan)

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
        )
        b = await b.with_venv(relocatable=True)
        b = await b.with_remote_dependencies()
        b = await b.with_workspace_files()
        b = b.with_container(await b.with_local_dependencies())
        return await b.venv()
