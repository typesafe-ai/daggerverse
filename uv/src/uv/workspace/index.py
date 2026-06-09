from typing import Annotated

from dagger import Doc, field, object_type


@object_type
class UvIndex:
    """A configured package index for a uv workspace."""

    name: Annotated[str, Doc("Index name.")] = field()
    url: Annotated[str, Doc("Index URL (used for resolving and downloading packages).")] = field()
    publish_url: Annotated[
        str | None,
        Doc("URL to use when publishing packages to this index (defaults to `url` if unset)."),
    ] = field(default=None)
