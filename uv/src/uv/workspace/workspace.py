from typing import Annotated

import dagger
from dagger import Doc, dag, field, function, object_type

from uv.utils import (
    _DEFAULT_VERSION,
    _PYPI_URL,
    is_exact_version,
    normalize_exact_version,
    parse_required_version_from_pyproject,
    parse_required_version_from_uv_toml,
    resolve_from_pypi_data,
)
from uv.workspace.audit import Audit


@object_type
class UvWorkspaceSource:
    """A self-contained uv workspace: the source files rooted at a ``uv.lock``.

    Currently carries the lockfile and the ``pyproject.toml`` that ``uv audit``
    requires alongside it; it will carry more of the workspace in the future.

    Named ``UvWorkspaceSource`` (not ``UvWorkspace``) to avoid colliding with the
    sibling ``uv-workspace`` module's main ``UvWorkspace`` object when both are
    composed in a parent module.
    """

    uv_lock: Annotated[
        dagger.File,
        Doc("The workspace's `uv.lock` file."),
    ] = field()

    pyproject: Annotated[
        dagger.File,
        Doc("The workspace's `pyproject.toml` file (required by `uv audit`)."),
    ] = field()

    uv_toml: Annotated[
        dagger.File | None,
        Doc("The workspace's `uv.toml` configuration file, if present."),
    ] = field(default=None)

    async def _required_version(self) -> str | None:
        """The `required-version` specifier declared by the workspace, if any.

        Prefers `uv.toml` (top-level) over `[tool.uv]` in `pyproject.toml`.
        """
        if self.uv_toml is not None:
            value = parse_required_version_from_uv_toml(await self.uv_toml.contents())
            if value is not None:
                return value
        return parse_required_version_from_pyproject(await self.pyproject.contents())

    @function
    async def uv_version(self) -> str:
        """The uv version this workspace requires, as a concrete image tag.

        Reads `required-version` (a PEP 440 specifier) from `uv.toml` or the
        `[tool.uv]` table of `pyproject.toml`, resolving ranges against PyPI.
        Falls back to the default version hardcoded in this module when unspecified.
        """
        specifier = await self._required_version()
        if specifier is None:
            return _DEFAULT_VERSION
        if is_exact_version(specifier):
            return normalize_exact_version(specifier)
        pypi_json = await dag.http(_PYPI_URL).contents()
        return resolve_from_pypi_data(pypi_json, specifier)

    @function
    def audit(
        self,
        image: Annotated[
            str,
            Doc("uv image reference to run the audit in."),
        ],
    ) -> Audit:
        """Audit this workspace's locked dependencies using the given uv image.

        The image is supplied by the caller so this type stays container-free
        (the runner owns image/version selection).
        """
        return Audit(
            uv_lock=self.uv_lock,
            pyproject=self.pyproject,
            image=image,
            uv_toml=self.uv_toml,
        )
