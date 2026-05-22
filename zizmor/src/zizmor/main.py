from typing import Annotated

import dagger
from dagger import Doc, dag, field, function, object_type

_DEFAULT_VERSION = "1.25.2"
_DEFAULT_IMAGE = "ghcr.io/zizmorcore/zizmor"


@object_type
class Zizmor:
    """Static analysis for GitHub Actions security."""

    ctr: Annotated[
        dagger.Container,
        Doc("Container with zizmor installed."),
    ] = field()

    @classmethod
    async def create(
        cls,
        ctr: Annotated[
            dagger.Container | None,
            Doc(
                "Container with zizmor installed. Defaults to the official ghcr.io/zizmorcore/zizmor image."
            ),
        ] = None,
        version: Annotated[
            str,
            Doc("zizmor image tag (only used when ctr is not provided)."),
        ] = _DEFAULT_VERSION,
    ) -> "Zizmor":
        if ctr is None:
            ctr = dag.container().from_(f"{_DEFAULT_IMAGE}:{version}")
        return cls(ctr=ctr)

    @function
    async def run(
        self,
        source: Annotated[
            dagger.Directory,
            Doc("Directory containing GitHub Actions workflows (parent to `.github`)."),
        ],
        github_token: Annotated[
            dagger.Secret | None,
            Doc(
                "GitHub token for online audits. Without it, zizmor runs in offline mode."
            ),
        ] = None,
        format: Annotated[
            str,
            Doc("Output format: plain, json, sarif, or github."),
        ] = "plain",
        persona: Annotated[
            str,
            Doc("Sensitivity level: regular, pedantic, or auditor."),
        ] = "regular",
        min_severity: Annotated[
            str | None,
            Doc("Minimum severity to report (e.g. low, medium, high)."),
        ] = None,
        min_confidence: Annotated[
            str | None,
            Doc("Minimum confidence to report (e.g. low, medium, high)."),
        ] = None,
        extra_args: Annotated[
            list[str] | None,
            Doc("Additional arguments to pass to zizmor."),
        ] = None,
    ) -> str:
        """Run zizmor on GitHub Actions workflow files.

        Exits non-zero if findings above the configured severity are found.
        """
        ctr = self.ctr.with_workdir("/work").with_mounted_directory("/work", source)

        if github_token is not None:
            ctr = ctr.with_secret_variable("GH_TOKEN", github_token)
        else:
            ctr = ctr.with_env_variable("ZIZMOR_OFFLINE", "true")

        args = ["zizmor", f"--format={format}", f"--persona={persona}"]

        if min_severity is not None:
            args.append(f"--min-severity={min_severity}")

        if min_confidence is not None:
            args.append(f"--min-confidence={min_confidence}")

        if extra_args:
            args.extend(extra_args)

        args.append(".")

        return await ctr.with_exec(args).combined_output()
