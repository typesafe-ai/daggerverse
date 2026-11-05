from __future__ import annotations

import asyncio
import posixpath
import tomllib
from typing import Annotated

import dagger
from dagger import Doc, check, collection, field, function, get, keys, object_type
from dagger_clients.core import CacheSharingMode, Container, Directory, Workspace, core
from dagger_clients.uv import uv

from dagger_zensical.args import SOURCE_EXCLUDE, Exclude

_CONFIG_FILE = "zensical.toml"
# Where zensical keeps its build cache, next to the config.
_CACHE_DIR = ".cache"
# What discovery reads: the docs configs and the pyproject.toml next to them.
_DISCOVERY_INCLUDE = [f"**/{_CONFIG_FILE}", "**/pyproject.toml", "**/.gitignore"]
_DEFAULT_GROUPS = ["docs"]


def _root_path(*names: str) -> str:
    return posixpath.normpath(posixpath.join(*names))


def _enclosing_workspace(path: str, workspaces: list[str]) -> str | None:
    """The innermost uv workspace path holding `path`, if any."""
    holding = [ws for ws in workspaces if ws == "." or path == ws or path.startswith(f"{ws}/")]
    return max(holding, key=lambda ws: 0 if ws == "." else ws.count("/") + 1, default=None)


def _project_name(pyproject_content: str | None) -> str | None:
    if pyproject_content is None:
        return None
    try:
        name = tomllib.loads(pyproject_content).get("project", {}).get("name")
    except tomllib.TOMLDecodeError:
        return None
    return name if isinstance(name, str) else None


def _site_name(config_content: str) -> str | None:
    name = tomllib.loads(config_content).get("project", {}).get("site_name")
    return name if isinstance(name, str) else None


def _docs_sources(config_content: str) -> list[str]:
    """The paths, relative to the config, that a build reads besides the config itself.

    `docs_dir` (default `docs`) and the theme's `custom_dir`, if set.
    """
    project = tomllib.loads(config_content).get("project", {})
    sources = [project.get("docs_dir", "docs")]
    custom_dir = project.get("theme", {}).get("custom_dir")
    if isinstance(custom_dir, str):
        sources.append(custom_dir)
    return sources


def _format_failure(exc: dagger.ExecError, path: str) -> str:
    """Human-readable message for a failed `zensical build`, with its output.

    A `dagger.ExecError` stringifies to just "exit code N", while zensical
    reports the broken links and missing pages on stdout or stderr.
    """
    seen: list[str] = []
    for stream in (exc.stdout, exc.stderr):
        text = (stream or "").strip()
        if text and text not in seen:
            seen.append(text)
    summary = f"zensical build failed for {path} (exit code {exc.exit_code})"
    detail = "\n".join(seen)
    return f"{summary}:\n\n{detail}" if detail else summary


@object_type
class ZensicalProject:
    """One Zensical docs site: a directory with a `zensical.toml`."""

    path: Annotated[
        str,
        Doc("Directory holding the `zensical.toml`, relative to the workspace root. `.` for the root."),
    ] = field()

    workspace: Annotated[
        str | None,
        Doc("Path of the uv workspace (uv.lock) the docs build syncs, or none if no workspace holds the docs."),
    ] = field()

    # Not `field()`s: internal state.
    ws: Workspace
    exclude: list[str]
    packages: list[str]
    groups: list[str]
    extras: list[str]
    strict: bool
    base_container: Container | None

    async def _container(self) -> tuple[Container, str]:
        """The uv workspace's environment with the docs sources, and where they are.

        zensical's build cache is a cache volume, one per site, so a rebuild
        after a docs change reuses what the previous build cached.
        """
        if self.workspace is None:
            msg = f"no uv workspace (uv.lock) holds the docs at {self.path!r}"
            raise ValueError(msg)
        builder = (
            uv(self.ws, exclude=self.exclude).workspace(self.workspace).builder(base_container=self.base_container)
        )
        if self.packages:
            builder = builder.with_packages(self.packages)
        if self.groups:
            builder = builder.with_groups(self.groups)
        if self.extras:
            builder = builder.with_extras(self.extras)
        ctr = builder.with_dependencies().container()

        config = self.ws.file(_root_path(self.path, _CONFIG_FILE))
        config_content = await config.contents()
        docs_path = _root_path(await ctr.workdir(), posixpath.relpath(self.path, self.workspace))
        ctr = ctr.with_file(posixpath.join(docs_path, _CONFIG_FILE), config).with_mounted_cache(
            posixpath.join(docs_path, _CACHE_DIR),
            core().cache_volume(f"zensical-{_site_name(config_content) or self.path}"),
            sharing=CacheSharingMode.LOCKED,
        )
        for source in _docs_sources(config_content):
            ctr = ctr.with_directory(
                posixpath.join(docs_path, source),
                self.ws.directory(_root_path(self.path, source), exclude=self.exclude, gitignore=True),
            )
        return ctr.with_workdir(docs_path), docs_path

    @function
    async def site(
        self,
        clean: Annotated[
            bool,
            Doc("Clear zensical's build cache before building (`zensical build --clean`)."),
        ] = False,
    ) -> Directory:
        """Build the docs with `zensical build` and return the generated site directory."""
        ctr, docs_path = await self._container()
        args = ["zensical", "build", "--config-file", _CONFIG_FILE]
        if clean:
            args.append("--clean")
        if self.strict:
            args.append("--strict")
        try:
            built = await ctr.with_exec(args).sync()
        except dagger.ExecError as exc:
            raise RuntimeError(_format_failure(exc, self.path)) from exc
        return built.directory(posixpath.join(docs_path, "site"))

    @check
    @function
    async def check(self) -> None:
        """Build the docs and fail on any error (any warning too, unless `strict` is off)."""
        await (await self.site()).sync()


@object_type
@collection
class ZensicalProjects:
    """Every Zensical docs site in the workspace, keyed by its path.

    `check` runs once per site, so each site is its own check that can be
    selected or skipped on its own.
    """

    paths: Annotated[
        list[str],
        Doc("Directories holding a `zensical.toml`, relative to the workspace root. `.` for the root."),
    ] = keys()

    items: list[ZensicalProject]

    @function
    @get
    def project(self, path: str) -> ZensicalProject:
        """The docs site at `path`."""
        for item in self.items:
            if item.path == path:
                return item
        msg = f"no zensical.toml at {path!r}"
        raise ValueError(msg)


@object_type
class Zensical:
    """Build [Zensical](https://zensical.org) docs sites.

    Every directory with a `zensical.toml` is a docs site. Each builds in the
    environment of the uv workspace that holds it, with the dependency groups
    and extras set by `groups` and `extras`, so docs plugins that import the
    project (e.g. an API reference) find it installed. When the docs directory
    is a workspace member's own, that member's package is installed.
    """

    items: list[ZensicalProject]

    @classmethod
    async def create(
        cls,
        ws: Workspace,
        groups: Annotated[
            list[str] | None,
            Doc("uv dependency groups to install for the build. They must provide `zensical`. Defaults to `docs`."),
        ] = None,
        extras: Annotated[
            list[str] | None,
            Doc("Extras of the docs' package to install for the build."),
        ] = None,
        exclude: Exclude = SOURCE_EXCLUDE,
        strict: Annotated[
            bool,
            Doc("Fail the build on warnings, such as broken links (`zensical build --strict`)."),
        ] = True,
        base_container: Annotated[
            Container | None,
            Doc(
                "Container the uv workspace's environment is built on, e.g. one carrying private index "
                "credentials. Defaults to the uv module's Debian uv image."
            ),
        ] = None,
    ) -> Zensical:
        """Discover the docs sites: one per `zensical.toml` outside `exclude`.

        Only the `zensical.toml`, `pyproject.toml` and `.gitignore` files are read.
        """
        discovered = ws.directory("/", include=_DISCOVERY_INCLUDE, exclude=exclude, gitignore=True)
        configs, workspaces = await asyncio.gather(
            discovered.glob(f"**/{_CONFIG_FILE}"), uv(ws, exclude=exclude).workspaces().keys()
        )
        groups = _DEFAULT_GROUPS if groups is None else groups

        async def _project(config: str) -> ZensicalProject:
            path = posixpath.dirname(config) or "."
            workspace = _enclosing_workspace(path, workspaces)
            packages: list[str] = []
            pyproject = _root_path(path, "pyproject.toml")
            if workspace is not None and path != workspace and await discovered.exists(pyproject):
                # The docs live in a workspace member: build that member, not the root's package.
                name = _project_name(await discovered.file(pyproject).contents())
                packages = [name] if name else []
            return ZensicalProject(
                path=path,
                workspace=workspace,
                ws=ws,
                exclude=exclude,
                packages=packages,
                groups=groups,
                extras=extras or [],
                strict=strict,
                base_container=base_container,
            )

        projects = await asyncio.gather(*(_project(config) for config in configs))
        return cls(items=sorted(projects, key=lambda project: project.path))

    @function
    def projects(self) -> ZensicalProjects:
        """Every Zensical docs site in the workspace."""
        return ZensicalProjects(paths=[item.path for item in self.items], items=self.items)
