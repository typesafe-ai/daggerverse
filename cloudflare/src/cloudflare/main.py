"""Cloudflare: deploy static sites to Cloudflare Pages."""

from typing import Annotated

import dagger
from dagger import Doc, field, function, object_type

from .pages import Pages
from .wrangler_container import DEFAULT_WRANGLER_VERSION


@object_type
class Cloudflare:
    """Cloudflare API client for Dagger pipelines.

    Authenticates with an API token and exposes sub-commands for each
    Cloudflare service.
    """

    token: Annotated[
        dagger.Secret,
        Doc("Cloudflare API token"),
    ] = field()

    wrangler_version: Annotated[
        str,
        Doc("Exact wrangler version to install"),
    ] = field(default=DEFAULT_WRANGLER_VERSION)

    @function
    def pages(self) -> Pages:
        """Cloudflare Pages: deploy static sites."""
        return Pages(token=self.token, wrangler_version=self.wrangler_version)
