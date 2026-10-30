import enum
from pathlib import Path
from typing import Annotated, Self

from dagger import Doc, enum_type, field, function, generate, object_type
from dagger_clients.core import Changeset, Container, Directory, Secret, Workspace, core

from zizmor.args import SOURCE_EXCLUDE, Exclude


@enum_type
class FixMode(enum.Enum):
    """Which fixes to apply."""

    SAFE = "safe"
    """Only apply fixes that are guaranteed to preserve workflow behavior."""

    UNSAFE_ONLY = "unsafe-only"
    """Only apply fixes that may change workflow behavior."""

    ALL = "all"
    """Apply all available fixes."""


def pinned_image(name: str) -> str:
    """The image reference in this package's `images/<name>/Dockerfile`, which Dependabot keeps current."""
    dockerfile = Path(__file__).resolve().parent / "images" / name / "Dockerfile"
    for line in dockerfile.read_text().splitlines():
        if line.startswith("FROM "):
            return line.split()[1]
    msg = f"no FROM line in {dockerfile}"
    raise ValueError(msg)


_DEFAULT_IMAGE, _DEFAULT_VERSION = pinned_image("zizmor").rsplit(":", 1)


@object_type
class Zizmor:
    """Static analysis for GitHub Actions security.

    `fix` is a generator: `dagger generate` applies zizmor's fixes, and
    `dagger check --generated` fails while it would change a file. It also
    fails on findings zizmor can't fix.
    """

    ctr: Annotated[
        Container,
        Doc("Container with zizmor installed."),
    ] = field()

    source: Annotated[
        Directory,
        Doc("The workspace's `.github` directory containing Actions workflows, less `exclude` and gitignored files."),
    ] = field()

    fix_mode: Annotated[FixMode, Doc("Which fixes `fix` applies.")] = field()
    persona: Annotated[str, Doc("Sensitivity level: regular, pedantic, or auditor.")] = field()
    min_severity: Annotated[str | None, Doc("Minimum severity to report (e.g. low, medium, high).")] = field()
    min_confidence: Annotated[str | None, Doc("Minimum confidence to report (e.g. low, medium, high).")] = field()
    github_token: Annotated[
        Secret | None,
        Doc("GitHub token for online audits. Without it, zizmor runs in offline mode."),
    ] = field()

    @classmethod
    async def create(
        cls,
        ws: Workspace,
        ctr: Annotated[
            Container | None,
            Doc("Container with zizmor installed. Defaults to the official ghcr.io/zizmorcore/zizmor image."),
        ] = None,
        version: Annotated[
            str,
            Doc("zizmor image tag (only used when ctr is not provided)."),
        ] = _DEFAULT_VERSION,
        fix_mode: Annotated[FixMode, Doc("Which fixes `fix` applies.")] = FixMode.SAFE,
        persona: Annotated[str, Doc("Sensitivity level: regular, pedantic, or auditor.")] = "regular",
        min_severity: Annotated[str | None, Doc("Minimum severity to report (e.g. low, medium, high).")] = None,
        min_confidence: Annotated[str | None, Doc("Minimum confidence to report (e.g. low, medium, high).")] = None,
        github_token: Annotated[
            Secret | None,
            Doc("GitHub token for online audits. Without it, zizmor runs in offline mode."),
        ] = None,
        exclude: Exclude = SOURCE_EXCLUDE,
    ) -> Self:
        if ctr is None:
            ctr = core().container().from_(f"{_DEFAULT_IMAGE}:{version}")
        # The workspace gets a new cache key on every run, and so does everything
        # loaded from it. Re-loading the source as a workspace of its own keys it
        # by its contents, so zizmor reruns only when a file changes.
        source = (
            ws.directory("/", include=[".github/**"], exclude=exclude, gitignore=True)
            .as_workspace()
            .directory("/.github")
        )
        return cls(
            ctr=ctr,
            source=source,
            fix_mode=fix_mode,
            persona=persona,
            min_severity=min_severity,
            min_confidence=min_confidence,
            github_token=github_token,
        )

    def _args(self, *extra: str) -> list[str]:
        args = ["zizmor", "--format=plain", f"--persona={self.persona}", *extra]
        if self.min_severity is not None:
            args.append(f"--min-severity={self.min_severity}")
        if self.min_confidence is not None:
            args.append(f"--min-confidence={self.min_confidence}")
        args.append(".")
        return args

    @generate
    @function
    def fix(self) -> Changeset:
        """Fix GitHub Actions security issues with `zizmor --fix`.

        Fails when findings zizmor can't fix remain.
        """
        ctr = self.ctr.with_workdir("/work").with_directory("/work/.github", self.source)
        if self.github_token is not None:
            ctr = ctr.with_secret_variable("GH_TOKEN", self.github_token)
        else:
            ctr = ctr.with_env_variable("ZIZMOR_OFFLINE", "true")
        fixed = (
            ctr.with_exec(self._args(f"--fix={self.fix_mode.value}", "--no-exit-codes"))
            # Audit the fixed workflows: only the findings left fail.
            .with_exec(self._args())
            .directory("/work/.github")
        )
        before = core().directory().with_directory(".github", self.source)
        after = core().directory().with_directory(".github", fixed)
        return after.changes(before)
