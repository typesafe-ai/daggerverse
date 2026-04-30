"""UvWorkspace: build minimal containers by parsing uv.lock for local dependencies."""

import posixpath
import tomllib
from typing import Annotated

import dagger
from dagger import Doc, dag, field, function, object_type

from uv_workspace._utils import (
    build_uv_sync_args,
    find_transitive_local_deps,
    parse_local_packages,
)


@object_type
class UvWorkspace:
    """Builds minimal project containers by parsing uv.lock to resolve local dependencies."""

    source_dir: Annotated[
        dagger.Directory,
        Doc("Source directory containing the workspace"),
    ] = field()

    base_container: Annotated[
        dagger.Container,
        Doc("Pre-configured container (with auth, system packages, etc.)"),
    ] = field()

    workspace_path: Annotated[
        str,
        Doc(
            "Path to workspace root (holding uv.lock and pyproject.toml) within source_dir"
        ),
    ] = field(default=".")

    async def _dagger_codegen(
        self, ws_dir: dagger.Directory, codegen_path: str
    ) -> dagger.Directory:
        """If `codegen_path` holds a Dagger module, run codegen and overlay.

        No-op when there's no `dagger.json` at `codegen_path`.

        Codegen runs against a synthesized directory containing only
        `dagger.json` (read as raw content and re-stamped via
        `with_new_file`). That's the minimal shape the Dagger Python SDK
        runtime needs — the codegen output (the SDK at `sdk/`) is
        determined by `engineVersion` + `sdk.source` in `dagger.json`
        and the engine schema, not by the user's source. Matches the
        primitive demonstrated by Dagger maintainers:

            directory | with-new-file dagger.json '...' |
                as-module-source | generated-context-directory

        Synthesizing keeps the codegen layer's cache stable across
        edits to the user's actual module source (`src/`, tests, etc.).
        """
        dagger_json_path = (
            "dagger.json"
            if codegen_path == "."
            else posixpath.join(codegen_path, "dagger.json")
        )
        if not await ws_dir.glob(dagger_json_path):
            return ws_dir
        dagger_json_contents = await ws_dir.file(dagger_json_path).contents()
        generated = (
            dag.directory()
            .with_new_file("dagger.json", dagger_json_contents)
            .as_module_source()
            .generated_context_directory()
        )
        return ws_dir.with_directory(codegen_path, generated)

    @function
    async def build(
        self,
        package: Annotated[
            str | None,
            Doc(
                "Package name; if set, only that package's transitive local deps are installed. Maps to `uv sync --package`"
            ),
        ] = None,
        extra: Annotated[
            list[str] | None,
            Doc("Extras to install; passed to `uv sync` as repeated `--extra`"),
        ] = None,
        group: Annotated[
            list[str] | None,
            Doc(
                "Dependency groups to install; passed to `uv sync` as repeated `--group`"
            ),
        ] = None,
        all_extras: Annotated[
            bool,
            Doc("Install every extra; maps to `uv sync --all-extras`"),
        ] = False,
        all_groups: Annotated[
            bool,
            Doc("Install every dependency group; maps to `uv sync --all-groups`"),
        ] = False,
        all_packages: Annotated[
            bool,
            Doc(
                "Install every workspace member; maps to `uv sync --all-packages`. Only meaningful in workspaces"
            ),
        ] = False,
        dagger_codegen: Annotated[
            bool,
            Doc(
                "If True (default), and the package being built has a "
                "`dagger.json`, run Dagger codegen and overlay the generated "
                "SDK before `uv sync`. This makes `[tool.uv.sources]` entries "
                'pointing at the generated tree (e.g. `dagger-io = { path = "sdk" }`) '
                "install correctly even though those paths are gitignored. "
                "No-op for non-Dagger projects. Pass False to skip."
            ),
        ] = True,
    ) -> dagger.Container:
        """Build a minimal container with deps installed for the given package.

        Parses uv.lock to find local workspace dependencies, then builds
        in two layers: remote deps first (cacheable), then local source.
        If package is specified, only that package's deps are installed (for workspaces).
        """
        ws_dir = (
            self.source_dir
            if self.workspace_path == "."
            else self.source_dir.directory(self.workspace_path)
        )
        uv_lock = ws_dir.file("uv.lock")

        lock_data = tomllib.loads(await uv_lock.contents())
        all_local = parse_local_packages(lock_data)
        needed_local = (
            find_transitive_local_deps(lock_data, package) if package else all_local
        )

        if dagger_codegen:
            codegen_path = (
                all_local[package] if package and package in all_local else "."
            )
            ws_dir = await self._dagger_codegen(ws_dir, codegen_path)

        pyproject_toml = ws_dir.file("pyproject.toml")

        ctr = self.base_container
        workdir = await ctr.workdir()

        ctr = (
            ctr.with_env_variable("UV_PROJECT_ENVIRONMENT", "/usr/local")
            .with_mounted_cache("/root/.cache/uv", dag.cache_volume("uv-cache"))
            .with_env_variable("UV_LINK_MODE", "copy")
            .with_env_variable("UV_FROZEN", "1")
        )

        ctr = ctr.with_file(
            posixpath.join(workdir, "pyproject.toml"), pyproject_toml
        ).with_file(posixpath.join(workdir, "uv.lock"), uv_lock)

        for pkg_name, path in sorted(needed_local.items()):
            src_name = pkg_name.replace("-", "_")
            ctr = (
                ctr.with_file(
                    posixpath.join(workdir, path, "pyproject.toml"),
                    ws_dir.file(posixpath.join(path, "pyproject.toml")),
                )
                .with_new_file(posixpath.join(workdir, path, "README.md"), "")
                .with_new_file(
                    posixpath.join(workdir, path, "src", src_name, "__init__.py"), ""
                )
            )

        uv_sync_base = build_uv_sync_args(
            package=package,
            extras=extra or [],
            groups=group or [],
            all_extras=all_extras,
            all_groups=all_groups,
            all_packages=all_packages,
        )

        ctr = ctr.with_exec([*uv_sync_base, "--no-install-local"])

        for path in sorted(needed_local.values()):
            ctr = ctr.with_directory(
                posixpath.join(workdir, path, "src"),
                ws_dir.directory(posixpath.join(path, "src")),
            )

        ctr = ctr.with_exec(uv_sync_base)

        return ctr
