import asyncio
import dataclasses
import logging
import posixpath
import tomllib
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Annotated

import dagger
from dagger import Doc, field, function, generate, object_type
from dagger.telemetry import get_tracer
from dagger_clients.core import Changeset, Container, Directory, File, ReturnType, core

from uv.args import (
    ActivateVenv,
    NoEditable,
    VenvRelocatable,
)
from uv.audit import (
    AuditCooldown,
    AuditStrategy,
    CooldownPolicy,
    UpdateStrategy,
    Vulnerability,
    add_exemptions,
    by_package,
    cooldown_cutoff,
    fixed_by,
    has_user_exemption,
    locked_version,
    minimal_fix,
    parse_report,
    prune_exemptions,
    upgrade_spec,
    upload_cutoff,
)
from uv.utils import (
    _DEFAULT_VERSION,
    _UV_CACHE_DIR,
    debian_image_ref,
    format_audit_failure,
    parse_indices,
    parse_required_version_from_pyproject,
    parse_required_version_from_uv_toml,
    python_request,
    resolve_specifier,
)
from uv.workspace.build import UvWorkspaceBuilder
from uv.workspace.build_backends.handler import BuildBackendHandler
from uv.workspace.index import UvIndex, dicts_to_indices, merge_indices, read_workspace_indices
from uv.workspace.package import UvPackageSource
from uv.workspace.plan import ProjectSource, UvSyncPlanner

logger = logging.getLogger(__name__)

_AUDIT = ["uv", "audit", "--frozen", "--preview-features", "audit,json-output"]

_VENV_WARNING = (
    "UV_PROJECT_ENVIRONMENT is not set, so uv creates a .venv. Unless you need one, set "
    "UV_PROJECT_ENVIRONMENT on the base container to its Python's prefix, such as /usr/local."
)


@object_type
class UvWorkspaceSource:
    """A self-contained uv workspace: the source files rooted at a `uv.lock`.

    Carries the source tree and the workspace's path within it (`"."` for a
    root workspace), relative to the Dagger workspace root. From those it derives everything it needs to `audit` the
    locked dependencies or get a `builder` for its packages, so
    the parent `Uv` stays a thin, container-free entrypoint.

    Every directory keeps workspace-root-relative paths, so builds can reach a
    sibling like `../my-dep` in a nested workspace. They are lazy: resolving
    and planning read only `manifests`, and a build loads only the `projects`
    it installs. Projects nested in those trees that nothing needs (e.g. test
    fixtures) are left out, so their edits don't invalidate this workspace's
    builds.

    Named `UvWorkspaceSource` (not `UvWorkspace`) to avoid colliding with
    the top-level `Uv` constructor when composed in a parent module.
    """

    source: Annotated[
        Directory,
        Doc("The workspace's members and their path dependencies, at workspace-root-relative paths."),
    ] = field()

    manifests: Annotated[
        Directory,
        Doc("Only what resolving the workspace reads: uv.lock, uv.toml and the members' pyproject.toml files."),
    ] = field()

    # Not a `field()`: every file the builds read, which `inputs` returns.
    inputs_dir: Directory

    path: Annotated[
        str,
        Doc("Workspace root path (holding uv.lock/pyproject.toml) within `source`. `.` for a root workspace."),
    ] = field(default=".")

    audit_strategy: AuditStrategy = field(default=UpdateStrategy.LATEST)

    audit_cooldown: AuditCooldown = field(default=CooldownPolicy.RESPECT)

    # Not `field()`s: internal state.
    # Each member's and local dependency's files, loaded only when a build installs that project.
    projects: list[ProjectSource] = dataclasses.field(default_factory=list)
    # The entries directly under the projects' `src/`, which mark a `src` layout.
    src_paths: list[str] = dataclasses.field(default_factory=list)
    # Handlers for the local packages' build backends, from `Uv.with_build_backend_handlers`.
    build_backend_handlers: list[BuildBackendHandler] = dataclasses.field(default_factory=list)

    def _ws_dir(self) -> Directory:
        return self.manifests if self.path == "." else self.manifests.directory(self.path)

    async def _uv_toml(self) -> File | None:
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
        to the module's pinned default when unspecified.
        """
        specifier = await self._required_version()
        if specifier is None:
            return _DEFAULT_VERSION
        return resolve_specifier(specifier)

    @function
    async def python_version(self) -> str | None:
        """The Python version pinned by the workspace's `.python-version`, if any.

        Returns its contents (e.g. `3.13.7`), or `None` when the file is absent.
        `builder` stages this pin so the environment and `uv sync` use the exact interpreter
        instead of resolving `requires-python`.
        """
        ws = self._ws_dir()
        if ".python-version" not in await ws.entries():
            return None
        return (await ws.file(".python-version").contents()).strip()

    @staticmethod
    def _member_paths_from(pyproject_data: dict, all_pyprojects: list[str]) -> list[str]:
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
        to `[[tool.uv.index]]` in `pyproject.toml`, matching uv's own
        precedence (`uv.toml` overrides the entire `[tool.uv]` section).

        When `include_from_members` is set, also reads `[[tool.uv.index]]`
        from every workspace member's `pyproject.toml` and merges them
        (member entries take precedence on name collision).
        """
        tracer = get_tracer()
        ws = self._ws_dir()

        pyproject_data = tomllib.loads(await ws.file("pyproject.toml").contents())

        with tracer.start_as_current_span("read workspace indices") as ws_span:
            ws_span.set_attribute("workspace.path", self.path)
            ws_raw = await read_workspace_indices(ws)
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
            manifests=self.manifests,
            workspace_path=self.path,
            package_path=path,
        )

    @generate
    @function
    async def audit(
        self,
        uv_version: Annotated[
            str | None,
            Doc(
                "uv version (Debian image tag) to audit and relock with. Defaults to the version "
                "detected from the workspace; ignored when `base_container` is set."
            ),
        ] = None,
        base_container: Annotated[
            Container | None,
            Doc(
                "Container with uv on PATH to audit and relock in, e.g. one carrying private index "
                "credentials. Defaults to the Debian uv image at `uv_version`."
            ),
        ] = None,
    ) -> Changeset:
        """Audit this workspace's locked dependencies and upgrade the vulnerable ones.

        Runs `uv audit --frozen` on the committed uv.lock (vulnerability data comes
        from the public OSV service), then relocks each vulnerable package that has a
        fix with `uv lock --upgrade-package`, per `audit_strategy` and `audit_cooldown`. Returns
        the uv.lock (and, for cooldown exemptions, uv settings) changes.

        Keeps a relock only when it fixes some advisory, and fails with uv's report when
        vulnerabilities remain and none could be fixed, so its staleness check
        (`dagger check --generated`) passes only for a clean workspace.

        The audit mounts only `manifests`, so a source edit leaves a clean workspace's
        audit cached. Relocking mounts `inputs`: uv builds members with dynamic metadata.
        """
        ws_dir = posixpath.join("/work", self.path)
        base = (
            (base_container or core().container().from_(debian_image_ref(uv_version or await self.uv_version())))
            .with_env_variable("UV_CACHE_DIR", _UV_CACHE_DIR)
            .with_mounted_cache(_UV_CACHE_DIR, core().cache_volume("uv-cache"))
            .with_workdir(ws_dir)
        )
        audited = base.with_directory("/work", self.manifests)
        ctr = base.with_directory("/work", self.inputs_dir)
        config = "uv.toml" if await self._uv_toml() is not None else "pyproject.toml"
        uv_toml = config == "uv.toml"

        # Drop the cooldown exemptions earlier runs wrote once the cooldown has caught up.
        config_text, lock_text = await asyncio.gather(
            self._ws_dir().file(config).contents(), self._ws_dir().file("uv.lock").contents()
        )
        pruned = prune_exemptions(config_text, cooldown_cutoff(lock_text, datetime.now(UTC)), uv_toml=uv_toml)
        if pruned != config_text:
            ctr = await self._relock(ctr.with_new_file(config, pruned), []) or ctr

        report = by_package(await self._audit_report(audited))
        fixed: list[Vulnerability] = []
        for package, vulns in sorted(report.items()):
            target = minimal_fix(vulns)
            if target is None:
                continue
            # Keep a partial upgrade (e.g. one the cooldown stops short of the fix) as long
            # as it fixes some advisories; one that doesn't would only churn uv.lock.
            upgraded = await self._relock(
                ctr, ["--upgrade-package", upgrade_spec(package, target, self.audit_strategy)]
            )
            fixes = await self._fixes(upgraded, vulns)
            if fixes:
                ctr = upgraded
            if (
                len(fixes) == len(vulns)
                or self.audit_cooldown is not CooldownPolicy.EXEMPT
                or has_user_exemption(await ctr.file(config).contents(), package, uv_toml=uv_toml)
            ):
                fixed += fixes
                continue
            exempted = await self._relock(
                ctr, ["--upgrade-package", f"{package}=={target}", "--exclude-newer-package", f"{package}=false"]
            )
            if exempted is not None:
                # Persist the narrowest exemption that admits the fix, so the lock's
                # options match the settings and a later `uv lock --locked` passes.
                cutoff = upload_cutoff(await exempted.file("uv.lock").contents(), package, target)
                config_text = add_exemptions(await exempted.file(config).contents(), {package: cutoff}, uv_toml=uv_toml)
                exempted = await self._relock(exempted.with_new_file(config, config_text), [])
            if exempted_fixes := await self._fixes(exempted, vulns):
                ctr, fixes = exempted, exempted_fixes
            fixed += fixes

        unfixed = [v for vulns in report.values() for v in vulns if v not in fixed]
        if unfixed and not fixed:
            await self._fail_audit(audited)
        if unfixed:
            ids = ", ".join(sorted(f"{v.package} {v.id}" for v in unfixed))
            logger.warning("uv audit: still vulnerable after upgrading %s: %s", self.path, ids)

        def relocked(name: str) -> File:
            return ctr.file(posixpath.join(ws_dir, name))

        after = self.manifests.with_file(posixpath.join(self.path, "uv.lock"), relocked("uv.lock")).with_file(
            posixpath.join(self.path, config), relocked(config)
        )
        return after.changes(self.manifests)

    async def _audit_report(self, ctr: Container) -> list[Vulnerability]:
        """The vulnerabilities `uv audit` reports for the lock in `ctr`."""
        audited = ctr.with_exec([*_AUDIT, "--output-format", "json"], expect=ReturnType.ANY)
        exit_code, stdout, stderr = await asyncio.gather(audited.exit_code(), audited.stdout(), audited.stderr())
        # 1 means vulnerabilities were found; anything else is uv failing.
        if exit_code not in (0, 1):
            raise RuntimeError(format_audit_failure(exit_code, stdout, stderr, self.path))
        return parse_report(stdout)

    async def _fail_audit(self, ctr: Container) -> None:
        """Rerun `uv audit` for its human-readable report and raise it."""
        try:
            await ctr.with_exec(_AUDIT).sync()
        except dagger.ExecError as exc:
            # The ExecError itself stringifies to a bare "exit code N"; fold uv's
            # report into the error so the findings show up there.
            raise RuntimeError(format_audit_failure(exc.exit_code, exc.stdout, exc.stderr, self.path)) from exc

    @staticmethod
    async def _relock(ctr: Container, args: list[str]) -> Container | None:
        """`ctr` relocked with `uv lock *args`, or `None` when uv can't resolve that."""
        locked = ctr.with_exec(["uv", "lock", *args], expect=ReturnType.ANY)
        if await locked.exit_code() != 0:
            logger.warning("uv lock %s failed:\n%s", " ".join(args), await locked.stderr())
            return None
        return locked

    @staticmethod
    async def _fixes(ctr: Container | None, vulns: list[Vulnerability]) -> list[Vulnerability]:
        """The advisories in `vulns` (one package) that `ctr`'s uv.lock fixes; none without `ctr`."""
        if ctr is None:
            return []
        version = locked_version(await ctr.file("uv.lock").contents(), vulns[0].package)
        return fixed_by(vulns, version) if version is not None else []

    @staticmethod
    async def _has_cache(ctr: Container, path: str) -> bool:
        """Whether `path` already holds a populated uv cache in the container.

        A deliberately prepared base may ship a warm cache baked at `UV_CACHE_DIR`;
        mounting an empty volume over it would hide it. An absent-or-empty directory
        means there's nothing to preserve, so the caller should mount the cache volume.
        """
        try:
            return len(await ctr.directory(path).entries()) > 0
        except dagger.DaggerError:
            return False

    @staticmethod
    async def _has_uv(ctr: Container) -> bool:
        with get_tracer().start_as_current_span("detect uv on PATH") as span:
            exit_code = await ctr.with_exec(["sh", "-c", "command -v uv"], expect=ReturnType.ANY).exit_code()
            found = exit_code == 0
            span.set_attribute("uv.found", found)
            return found

    @staticmethod
    async def _has_python(ctr: Container, python: str | None) -> bool:
        """Whether the base has its own Python that satisfies the workspace's request."""
        with get_tracer().start_as_current_span("find a system Python") as span:
            argv = ["uv", "python", "find", "--system", *([python] if python else [])]
            found = await ctr.with_exec(argv, expect=ReturnType.ANY).exit_code() == 0
            span.set_attribute("python.found", found)
            return found

    @classmethod
    async def _with_project_environment(
        cls, ctr: Container, python: str | None, *, default_base: bool, venv_relocatable: bool, activate_venv: bool
    ) -> Container:
        """Choose and prepare the environment `uv sync` installs into.

        Runs before the workspace's `pyproject.toml` and `uv.lock` are copied in, so
        these layers survive dependency changes. In order:

        - `venv_relocatable`: a relocatable `.venv`, whatever the base sets.
        - The base sets `UV_PROJECT_ENVIRONMENT`: uv installs there; nothing to do.
        - The default base, or a base without a Python that satisfies `python`: a
          uv-managed Python, installed into directly (no `.venv`). The default
          image's `python3` belongs to Debian.
        - A base with a suitable Python: a `.venv` on it, with a warning, as that's
          what uv does.
        """
        tracer = get_tracer()
        python_args = ["--python", python] if python else []
        if venv_relocatable:
            with tracer.start_as_current_span("create relocatable .venv"):
                ctr = await (
                    ctr.without_env_variable("UV_PROJECT_ENVIRONMENT")
                    .with_exec(["uv", "venv", "--relocatable", *python_args])
                    .sync()
                )
        elif await ctr.env_variable("UV_PROJECT_ENVIRONMENT"):
            return ctr
        elif default_base or not await cls._has_python(ctr, python):
            with tracer.start_as_current_span("install uv-managed Python") as span:
                request = [python] if python else []
                ctr = await ctr.with_exec(["uv", "python", "install", *request]).sync()
                interpreter = (await ctr.with_exec(["uv", "python", "find", *request]).stdout()).strip()
                prefix = (await ctr.with_exec([interpreter, "-c", "import sys; print(sys.prefix)"]).stdout()).strip()
                span.set_attribute("python.prefix", prefix)
            return ctr.with_env_variable("UV_PROJECT_ENVIRONMENT", prefix).with_env_variable(
                "PATH", f"{prefix}/bin:${{PATH}}", expand=True
            )
        else:
            with tracer.start_as_current_span("create .venv (UV_PROJECT_ENVIRONMENT is not set)") as span:
                logger.warning(_VENV_WARNING)
                span.add_event("warning", {"message": _VENV_WARNING})
                ctr = await ctr.with_exec(["uv", "venv", *python_args]).sync()
        if activate_venv:
            venv = posixpath.join(await ctr.workdir(), ".venv")
            ctr = ctr.with_env_variable("VIRTUAL_ENV", venv).with_env_variable(
                "PATH", f"{venv}/bin:${{PATH}}", expand=True
            )
        return ctr

    async def _default_base_container(self) -> Container:
        """A Debian-based uv image pinned to this workspace's uv version.

        Uses the Debian variant rather than the distroless `uv:<version>` image
        because the latter is `FROM scratch`: `uv sync` can't provision a managed
        Python there (no libc). On Debian, uv downloads Python on first sync and
        ca-certificates are already present. Pass an explicit `base_container` to
        build on your own auth/system-package layers.
        """
        return core().container().from_(debian_image_ref(await self.uv_version())).with_workdir("/work")

    def _planner(self) -> UvSyncPlanner:
        return UvSyncPlanner(
            manifests=self.manifests,
            source=self.source,
            inputs_dir=self.inputs_dir,
            workspace_path=self.path,
            projects=self.projects,
            src_paths=self.src_paths,
            handlers=self.build_backend_handlers,
        )

    @function
    def inputs(self) -> Directory:
        """Every file a `builder` reads, at workspace-root-relative paths.

        `Uv.workspace` loads the workspace's directories from just these files.
        A module that reads more of the workspace can merge its own files into
        them and hand the result on with `as_workspace()`.
        """
        return self.inputs_dir

    @function
    async def builder(
        self,
        base_container: Annotated[
            Container | None,
            Doc(
                "Container to build on top of (auth, system packages, etc.). Defaults to "
                "a Debian-based uv image at the workspace's uv version (uv provisions Python on demand)."
            ),
        ] = None,
        no_editable: NoEditable = False,
        venv_relocatable: VenvRelocatable = False,
        activate_venv: ActivateVenv = True,
        auto_install_uv: Annotated[
            bool,
            Doc(
                "Automatically install the uv binary if it is not already on $PATH. "
                "Set to False if your base_container already ships uv."
            ),
        ] = True,
    ) -> UvWorkspaceBuilder:
        """A builder for this workspace's packages, with nothing installed yet.

        Mounts the uv cache, prepares the environment uv installs into, and
        copies the root pyproject.toml and uv.lock into the container.

        The environment follows `UV_PROJECT_ENVIRONMENT` when the base sets it.
        Otherwise the default base, or one without a suitable Python, gets a
        uv-managed Python that packages install into directly, and a base with
        a suitable Python gets a `.venv` (activated unless `activate_venv=False`). Set
        `UV_PROJECT_ENVIRONMENT` on the base to its Python's prefix to avoid a
        redundant `.venv`.

        Select what to build on the returned `UvWorkspaceBuilder` (`with_packages`,
        `with_extras`, `with_groups`, ...); it defaults to the workspace root's
        package. Then `with_dependencies()` installs everything, or run its steps
        individually: `with_remote_dependencies()`, `with_workspace_files()`,
        then `with_local_dependencies()`. Skip `with_remote_dependencies()` when
        another tool (e.g. `pulumi install`) handles dependency installation.
        Finish with `container()`, `venv()` (with `venv_relocatable=True`), or
        `build()` for the packages' sdists and wheels.
        """
        with get_tracer().start_as_current_span("prepare workspace builder") as span:
            span.set_attribute("workspace.path", self.path)
            span.set_attribute("build.default_base_container", base_container is None)

            ctr = base_container if base_container is not None else await self._default_base_container()
            cache_dir = await ctr.env_variable("UV_CACHE_DIR")
            if cache_dir:
                # The base already points uv at a cache dir. Mount the cache volume there
                # unless the base actually ships a populated cache: don't shadow a warm,
                # deliberately prepared one with an empty mount. Crucially, a base built by
                # this module carries a baked UV_CACHE_DIR but *no* directory there (Dagger
                # mounts don't survive a push), so it gets re-mounted: without the mount,
                # `uv sync` would materialize the cache into a real image layer
                # (non-reproducible, ~2x size) instead of writing to the volume.
                if not await self._has_cache(ctr, cache_dir):
                    ctr = ctr.with_mounted_cache(cache_dir, core().cache_volume("uv-cache"))
            else:
                cache_dir = _UV_CACHE_DIR
                ctr = ctr.with_env_variable("UV_CACHE_DIR", cache_dir).with_mounted_cache(
                    cache_dir, core().cache_volume("uv-cache")
                )
            workdir = await ctr.workdir()

            # Place workspace files at their real path within the source tree
            # so that relative paths in uv.lock resolve correctly.
            ws_ctr_path = posixpath.join(workdir, self.path) if self.path != "." else workdir
            ctr = ctr.with_workdir(ws_ctr_path)
            python_version = await self.python_version()
            if python_version:
                ctr = ctr.with_new_file(posixpath.join(ws_ctr_path, ".python-version"), python_version)
            builder = UvWorkspaceBuilder(container=ctr, planner=self._planner(), no_editable=no_editable)
            if auto_install_uv and not await self._has_uv(ctr):
                ctr = (await builder.with_uv(await self.uv_version())).container

            ws_dir = self._ws_dir()
            pyproject_contents, uv_lock_contents = await asyncio.gather(
                ws_dir.file("pyproject.toml").contents(),
                ws_dir.file("uv.lock").contents(),
            )
            ctr = await self._with_project_environment(
                ctr,
                python_request(python_version, pyproject_contents),
                default_base=base_container is None,
                venv_relocatable=venv_relocatable,
                activate_venv=activate_venv,
            )
            ctr = ctr.with_new_file(posixpath.join(ws_ctr_path, "pyproject.toml"), pyproject_contents).with_new_file(
                posixpath.join(ws_ctr_path, "uv.lock"), uv_lock_contents
            )
            return builder.with_container(await ctr.sync())
