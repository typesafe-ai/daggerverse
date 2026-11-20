"""Virtual environments filled with `uv pip install`, outside any uv workspace."""

import dataclasses
import posixpath
from typing import Annotated, Self

from dagger import Doc, field, function, object_type
from dagger_clients.core import Container, File

from uv.workspace.venv import UvVenv

# Where `with_pip_install` mounts its requirements files.
_REQUIREMENTS_DIR = "/tmp/uv-requirements"


@object_type
class UvPipVenv:
    """A relocatable virtual environment filled with `uv pip install`, from packages or
    requirements files rather than a uv workspace's lock: for example a released
    application's pinned closure, as `uv export` writes it.

    Its `venv` is a `UvVenv`, to copy into another container with `into`.

    Learn more in the [docs](https://daggerverse.docs.typesafe.ai/uv/virtual-environments/)."""

    container: Annotated[Container, Doc("The container holding the venv, with uv and its cache.")] = field()
    path: Annotated[str, Doc("Absolute path of the venv in `container`.")] = field()

    @function
    def with_pip_install(
        self,
        packages: Annotated[list[str] | None, Doc("Requirement specifiers, e.g. `tabulate==0.9.0`.")] = None,
        requirements: Annotated[
            list[File] | None, Doc("Requirements files, passed as repeated `-r`, e.g. `uv export` output.")
        ] = None,
        no_deps: Annotated[
            bool, Doc("Install exactly what is listed (`--no-deps`), as for a complete pinned closure.")
        ] = False,
        args: Annotated[list[str] | None, Doc("More `uv pip install` arguments, e.g. `--index-url`.")] = None,
    ) -> Self:
        """Install `packages` and `requirements` into the venv, in one `uv pip install`."""
        ctr = self.container
        argv = ["uv", "pip", "install", "--python", posixpath.join(self.path, "bin", "python")]
        if no_deps:
            argv.append("--no-deps")
        for index, requirement in enumerate(requirements or []):
            mounted = posixpath.join(_REQUIREMENTS_DIR, f"{index}.txt")
            ctr = ctr.with_mounted_file(mounted, requirement)
            argv += ["-r", mounted]
        argv += [*(args or []), *(packages or [])]
        return dataclasses.replace(self, container=ctr.with_exec(argv))

    @function
    async def venv(self) -> UvVenv:
        """The venv, with the uv-managed Python it links against, to copy into another container."""
        return await UvVenv.create(self.container, self.path)
