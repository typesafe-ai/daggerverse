from typing import Annotated

import dagger
from dagger import Doc, dag, field, function, object_type


@object_type
class Audit:
    """Runs ``uv audit`` for a single workspace in a given uv image."""

    uv_lock: Annotated[
        dagger.File,
        Doc("The workspace's uv.lock file."),
    ] = field()

    pyproject: Annotated[
        dagger.File,
        Doc("The workspace's pyproject.toml file."),
    ] = field()

    image: Annotated[
        str,
        Doc("uv image reference to run the audit in (must provide a `uv` binary)."),
    ] = field()

    uv_toml: Annotated[
        dagger.File | None,
        Doc("The workspace's uv.toml configuration file, if present."),
    ] = field(default=None)

    @function
    async def run(self) -> None:
        """Run ``uv audit --frozen`` for this workspace.

        Audits straight from the committed uv.lock without re-resolving against
        package indexes (``--frozen``); vulnerability data comes from the public
        OSV service, so no index credentials are required. A failing audit
        (non-zero exit, e.g. vulnerabilities found) raises ``dagger.ExecError``,
        whose ``stdout``/``stderr`` carry uv's report; the runner folds that into
        the trace error (see ``Uv.audit``).
        """
        ctr = (
            dag.container()
            .from_(self.image)
            # the bare astral image ships uv at /uv (not on PATH); prepending /
            # makes a plain `uv` work for both it and images with uv on PATH.
            .with_env_variable("PATH", "/:${PATH}", expand=True)
            .with_workdir("/work")
            .with_file("/work/uv.lock", self.uv_lock)
            .with_file("/work/pyproject.toml", self.pyproject)
        )
        if self.uv_toml is not None:
            ctr = ctr.with_file("/work/uv.toml", self.uv_toml)
        await ctr.with_exec(["uv", "audit", "--frozen"]).sync()
