import sys
from typing import Annotated

import dagger
from dagger import DefaultPath, Doc, Ignore, check, dag, field, function, object_type

_DEFAULT_VERSION = "4.0.0"
_RELEASES_URL = "https://github.com/suzuki-shunsuke/pinact/releases/download"
_CONFIG_FILE = ".pinact.yaml"


@object_type
class Pinact:
    """Pin GitHub Actions to full-length commit SHAs."""

    version: Annotated[
        str,
        Doc("pinact version to use."),
    ] = field(default=_DEFAULT_VERSION)

    async def _container(
        self,
        source: dagger.Directory,
        github_token: dagger.Secret | None = None,
        config: dagger.File | None = None,
    ) -> dagger.Container:
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
            .with_mounted_directory("/work/.github", source.directory(".github"))
        )

        if github_token is not None:
            ctr = ctr.with_secret_variable("GITHUB_TOKEN", github_token)

        if config is not None:
            ctr = ctr.with_mounted_file(f"/work/{_CONFIG_FILE}", config)
        elif await source.glob(_CONFIG_FILE):
            ctr = ctr.with_mounted_file(
                f"/work/{_CONFIG_FILE}", source.file(_CONFIG_FILE)
            )

        return ctr

    @staticmethod
    def _args(
        *,
        fix: bool,
        verify_comment: bool,
        extra_args: list[str] | None,
    ) -> list[str]:
        args = ["pinact", "run"]
        if not fix:
            args.append("--check")
        if verify_comment:
            args.append("--verify-comment")
        if extra_args:
            args.extend(extra_args)
        return args

    @check
    @function
    async def lint(
        self,
        source: Annotated[
            dagger.Directory,
            Doc("Repository root containing `.github` and optionally `.pinact.yaml`."),
            DefaultPath("."),
            Ignore(["*", "!.github", "!.pinact.yaml"]),
        ],
        config: Annotated[
            dagger.File | None,
            Doc("Non-default path to `.pinact.yaml` configuration file."),
        ] = None,
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
        ctr = await self._container(source, github_token, config)
        args = self._args(
            fix=False, verify_comment=verify_comment, extra_args=extra_args
        )
        return await ctr.with_exec(args).combined_output()

    @function
    async def fix(
        self,
        source: Annotated[
            dagger.Directory,
            Doc("Repository root containing `.github` and optionally `.pinact.yaml`."),
            DefaultPath("."),
            Ignore(["*", "!.github", "!.pinact.yaml"]),
        ],
        github_token: Annotated[
            dagger.Secret,
            Doc("GitHub token for resolving SHAs via the GitHub API."),
        ],
        config: Annotated[
            dagger.File | None,
            Doc("Non-default path to `.pinact.yaml` configuration file."),
        ] = None,
        verify_comment: Annotated[
            bool,
            Doc("Verify and fix version comments."),
        ] = True,
        extra_args: Annotated[
            list[str] | None,
            Doc("Additional arguments to pass to `pinact run`."),
        ] = None,
    ) -> dagger.Changeset:
        """Pin unpinned GitHub Actions to full-length commit SHAs.

        Returns a Changeset applied to the host.
        """
        ctr = await self._container(source, github_token, config)
        args = self._args(
            fix=True, verify_comment=verify_comment, extra_args=extra_args
        )
        result = ctr.with_exec(args, expect=dagger.ReturnType.ANY)
        output = await result.combined_output()
        if output.strip():
            sys.stderr.write(output)
        fixed = result.directory("/work/.github")
        after = source.with_directory(".github", fixed)
        return after.changes(source)
