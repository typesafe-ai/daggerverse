"""Cloudflare Pages: direct-upload deployment via wrangler."""

from typing import Annotated

import dagger
from dagger import Doc, dag, field, function, object_type

from .wrangler_container import DEFAULT_WRANGLER_VERSION, wrangler_container


@object_type
class Pages:
    """Deploy a static site directory to a Cloudflare Pages project."""

    token: Annotated[
        dagger.Secret,
        Doc("Cloudflare API token with Pages:Edit"),
    ] = field()

    wrangler_version: Annotated[
        str,
        Doc("Exact wrangler version to install"),
    ] = field(default=DEFAULT_WRANGLER_VERSION)

    @function
    async def deploy(
        self,
        directory: Annotated[
            dagger.Directory,
            Doc("Directory containing the built static site"),
        ],
        project: Annotated[
            str,
            Doc("Cloudflare Pages project name"),
        ],
        account_id: Annotated[
            str,
            Doc("Cloudflare account ID"),
        ],
        branch: Annotated[
            str,
            Doc("Branch name for the deployment (determines production vs preview)"),
        ] = "main",
        prefix: Annotated[
            str | None,
            Doc("Serve the site under this URL path prefix (e.g. 'ts-dagger-module')"),
        ] = None,
    ) -> str:
        """Upload a directory to Cloudflare Pages and return the deployment URL."""
        site = directory
        if prefix is not None:
            site = dag.directory().with_directory(prefix, directory)
        return await (
            wrangler_container(self.token, self.wrangler_version)
            .with_env_variable("CLOUDFLARE_ACCOUNT_ID", account_id)
            .with_mounted_directory("/site", site)
            .with_exec(
                [
                    "wrangler",
                    "pages",
                    "deploy",
                    "/site",
                    f"--project-name={project}",
                    f"--branch={branch}",
                ]
            )
            .stdout()
        )
