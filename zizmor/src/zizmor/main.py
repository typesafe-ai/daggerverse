import enum
import sys
from typing import Annotated

import dagger
from dagger import DefaultPath, Doc, check, dag, enum_type, field, function, object_type


@enum_type
class FixMode(enum.Enum):
    """Which fixes to apply."""

    SAFE = "safe"
    """Only apply fixes that are guaranteed to preserve workflow behavior."""

    UNSAFE_ONLY = "unsafe-only"
    """Only apply fixes that may change workflow behavior."""

    ALL = "all"
    """Apply all available fixes."""


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

    def _container(
        self,
        source: dagger.Directory,
        github_token: dagger.Secret | None = None,
    ) -> dagger.Container:
        ctr = self.ctr.with_workdir("/work").with_directory("/work/.github", source)

        if github_token is not None:
            ctr = ctr.with_secret_variable("GH_TOKEN", github_token)
        else:
            ctr = ctr.with_env_variable("ZIZMOR_OFFLINE", "true")

        return ctr

    @staticmethod
    def _args(
        *,
        fix: FixMode | None,
        persona: str,
        format: str,
        min_severity: str | None,
        min_confidence: str | None,
        extra_args: list[str] | None,
    ) -> list[str]:
        args = ["zizmor", f"--format={format}", f"--persona={persona}"]
        if fix is not None:
            args.extend([f"--fix={fix.value}", "--no-exit-codes"])
        if min_severity is not None:
            args.append(f"--min-severity={min_severity}")
        if min_confidence is not None:
            args.append(f"--min-confidence={min_confidence}")
        if extra_args:
            args.extend(extra_args)
        args.append(".")
        return args

    @check
    @function
    async def lint(
        self,
        source: Annotated[
            dagger.Directory,
            Doc("The `.github` directory containing Actions workflows."),
            DefaultPath(".github"),
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
        ctr = self._container(source, github_token)
        args = self._args(
            fix=None,
            persona=persona,
            format=format,
            min_severity=min_severity,
            min_confidence=min_confidence,
            extra_args=extra_args,
        )
        return await ctr.with_exec(args).combined_output()

    @function
    async def fix(
        self,
        source: Annotated[
            dagger.Directory,
            Doc("The `.github` directory containing Actions workflows."),
            DefaultPath(".github"),
        ],
        mode: Annotated[
            FixMode,
            Doc("Which fixes to apply."),
        ] = FixMode.SAFE,
        github_token: Annotated[
            dagger.Secret | None,
            Doc(
                "GitHub token for online audits. Without it, zizmor runs in offline mode."
            ),
        ] = None,
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
    ) -> dagger.Changeset:
        """Auto-fix GitHub Actions security issues found by zizmor.

        Returns a Changeset applied to the host.
        """
        ctr = self._container(source, github_token)
        args = self._args(
            fix=mode,
            persona=persona,
            format="plain",
            min_severity=min_severity,
            min_confidence=min_confidence,
            extra_args=extra_args,
        )
        result = ctr.with_exec(args)
        output = await result.combined_output()
        if output.strip():
            sys.stderr.write(output)
        fixed = result.directory("/work/.github")
        before = dag.directory().with_directory(".github", source)
        after = dag.directory().with_directory(".github", fixed)
        return after.changes(before)
