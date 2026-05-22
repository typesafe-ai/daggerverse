from typing import Annotated

import dagger
from dagger import Doc, dag, field, function, object_type

_DEFAULT_VERSION = "3.10.1"
_RELEASES_URL = "https://github.com/suzuki-shunsuke/pinact/releases/download"


@object_type
class Pinact:
    """Pin GitHub Actions to full-length commit SHAs."""

    version: Annotated[
        str,
        Doc("pinact version to use."),
    ] = field(default=_DEFAULT_VERSION)

    @function
    async def run(
        self,
        source: Annotated[
            dagger.Directory,
            Doc("Directory containing GitHub Actions workflows."),
        ],
        github_token: Annotated[
            dagger.Secret | None,
            Doc("GitHub token for resolving SHAs via the GitHub API."),
        ] = None,
        verify_comment: Annotated[
            bool,
            Doc("Verify that version comments match the pinned SHA."),
        ] = False,
        extra_args: Annotated[
            list[str] | None,
            Doc("Additional arguments to pass to `pinact run`."),
        ] = None,
    ) -> str:
        """Check that GitHub Actions are pinned to full-length commit SHAs.

        Exits non-zero if unpinned actions are found.
        """
        platform = await dag.default_platform()
        arch = "arm64" if "arm64" in str(platform) else "amd64"
        archive_url = f"{_RELEASES_URL}/v{self.version}/pinact_linux_{arch}.tar.gz"

        ctr = (
            dag.container()
            .from_("alpine:3.21")
            .with_exec(["apk", "add", "--no-cache", "curl"])
            .with_exec(
                [
                    "sh",
                    "-c",
                    f"curl -fsSL '{archive_url}' | tar xz -C /usr/local/bin pinact",
                ]
            )
            .with_workdir("/work")
            .with_mounted_directory("/work", source)
        )

        if github_token is not None:
            ctr = ctr.with_secret_variable("GITHUB_TOKEN", github_token)

        args = ["pinact", "run", "--check"]

        if verify_comment:
            args.append("--verify")

        if extra_args:
            args.extend(extra_args)

        return await ctr.with_exec(args).combined_output()
