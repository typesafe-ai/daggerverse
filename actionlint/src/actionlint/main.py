from typing import Annotated

import dagger
from dagger import DefaultPath, Doc, check, dag, field, function, object_type

_DEFAULT_VERSION = "1.7.12"
_BASE_IMAGE = "alpine:3.21"
# shellcheck's author's official image; copy the static binary out of it.
_SHELLCHECK_IMAGE = "docker.io/koalaman/shellcheck:stable"


def release_url(version: str, arch: str) -> str:
    """URL of the official actionlint release tarball for a version and arch."""
    return (
        "https://github.com/rhysd/actionlint/releases/download/"
        f"v{version}/actionlint_{version}_linux_{arch}.tar.gz"
    )


@object_type
class Actionlint:
    """Lint GitHub Actions workflows with actionlint.

    Complements security scanners like zizmor: actionlint checks workflow
    syntax, ``${{ }}`` expression types, runner labels, ``cron``/glob patterns,
    deprecated commands, and runs shellcheck on ``run:`` scripts.
    """

    ctr: Annotated[
        dagger.Container,
        Doc("Container with actionlint (and shellcheck) installed."),
    ] = field()

    @classmethod
    async def create(
        cls,
        ctr: Annotated[
            dagger.Container | None,
            Doc(
                "Container with actionlint installed. Defaults to an image built "
                "from the official actionlint release binary plus shellcheck."
            ),
        ] = None,
        version: Annotated[
            str,
            Doc("actionlint version to install (only used when ctr is not provided)."),
        ] = _DEFAULT_VERSION,
    ) -> "Actionlint":
        if ctr is None:
            ctr = await cls._build(version)
        return cls(ctr=ctr)

    @staticmethod
    async def _build(version: str) -> dagger.Container:
        """Compose actionlint + shellcheck from their official sources.

        Pulls the official actionlint release binary for the engine's
        architecture and the shellcheck binary from its author's image, so we
        don't depend on a prebaked combined image.
        """
        arch = (await dag.default_platform()).rsplit("/", 1)[-1]
        url = release_url(version, arch)
        actionlint_bin = (
            dag.container()
            .from_(_BASE_IMAGE)
            .with_mounted_file("/tmp/actionlint.tgz", dag.http(url))
            .with_exec(
                [
                    "tar",
                    "-xzf",
                    "/tmp/actionlint.tgz",
                    "-C",
                    "/usr/local/bin",
                    "actionlint",
                ]
            )
            .file("/usr/local/bin/actionlint")
        )
        shellcheck_bin = (
            dag.container().from_(_SHELLCHECK_IMAGE).file("/bin/shellcheck")
        )
        return (
            dag.container()
            .from_(_BASE_IMAGE)
            .with_file("/usr/local/bin/actionlint", actionlint_bin)
            .with_file("/usr/local/bin/shellcheck", shellcheck_bin)
        )

    @check
    @function
    async def lint(
        self,
        source: Annotated[
            dagger.Directory,
            Doc("The `.github` directory containing Actions workflows."),
            DefaultPath(".github"),
        ],
        extra_args: Annotated[
            list[str] | None,
            Doc("Additional arguments to pass to actionlint."),
        ] = None,
    ) -> None:
        """Run actionlint on GitHub Actions workflow files.

        Exits non-zero when problems are found. Runs shellcheck on ``run:``
        scripts.
        """
        # actionlint auto-discovery walks up for a Git project root, which a
        # mounted directory lacks. Pass the workflow files explicitly instead.
        workflows = sorted(
            [
                *await source.glob("workflows/*.yml"),
                *await source.glob("workflows/*.yaml"),
            ]
        )
        if not workflows:
            return
        ctr = self.ctr.with_workdir("/work").with_directory("/work/.github", source)
        args = ["actionlint", *(f".github/{w}" for w in workflows)]
        if extra_args:
            args.extend(extra_args)
        await ctr.with_exec(args)
