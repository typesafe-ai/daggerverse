from typing import Annotated

import dagger
from dagger import Doc, field, object_type

from uv.utils import parse_indices


@object_type
class UvIndex:
    """A configured package index for a uv workspace."""

    name: Annotated[str, Doc("Index name.")] = field()
    url: Annotated[str, Doc("Index URL (used for resolving and downloading packages).")] = field()
    publish_url: Annotated[
        str | None,
        Doc("URL to use when publishing packages to this index (defaults to `url` if unset)."),
    ] = field(default=None)
    default: Annotated[
        bool,
        Doc("When true, this index replaces PyPI as the default index."),
    ] = field(default=False)
    explicit: Annotated[
        bool,
        Doc("When true, packages are only installed from this index if explicitly pinned to it."),
    ] = field(default=False)
    authenticate: Annotated[
        str | None,
        Doc("Credential handling: 'always', 'never', or null (try unauthenticated first, then authenticate)."),
    ] = field(default=None)
    format: Annotated[
        str | None,
        Doc("Index format: 'flat' for flat directories/HTML lists, null for standard PEP 503 registries."),
    ] = field(default=None)


def dicts_to_indices(entries: list[dict]) -> list[UvIndex]:
    return [UvIndex(**e) for e in entries]


def merge_indices(
    base: list[dict],
    override: list[dict],
) -> list[UvIndex]:
    """Merge two index lists, deduplicating by name (override wins)."""
    by_name: dict[str, dict] = {}
    for entry in base:
        by_name[entry["name"]] = entry
    for entry in override:
        by_name[entry["name"]] = entry
    merged = sorted(by_name.values(), key=lambda e: e["name"] or "")
    return dicts_to_indices(merged)


async def read_workspace_indices(ws_dir: dagger.Directory) -> list[dict]:
    """Read raw index dicts from workspace-level config (uv.toml or pyproject.toml)."""
    if "uv.toml" in await ws_dir.entries():
        return parse_indices(await ws_dir.file("uv.toml").contents(), uv_toml=True)
    return parse_indices(await ws_dir.file("pyproject.toml").contents())
