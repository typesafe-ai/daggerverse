import posixpath
from typing import Annotated, Self

import dagger
from dagger import Doc, field, function, object_type

from uv.utils import parse_pyvenv_cfg


@object_type
class UvVenv:
    """A relocatable uv virtual environment bundled with the uv-managed Python it needs.

    A venv never contains the standard library — ``.venv/bin/python`` resolves to
    a base interpreter whose ``lib/`` provides it. This pairs the venv with that
    interpreter so the two can be dropped into a fresh container (one without any
    Python of its own) and run without uv.

    Only valid for a venv created with ``with_venv(relocatable=True)`` against a
    *uv-managed* (standalone) Python: uv's standalone Pythons are themselves
    relocatable, and a relocatable venv keeps working when its directory is moved.
    The venv may be mounted at any path, but the Python must be remounted at
    ``python_path`` (the absolute location its ``pyvenv.cfg`` records).

    The bundled Python is the python-build-standalone variant uv selected for the
    base image's platform/libc (set by ``base_container`` at build time), so the
    target container must match it: a Debian (glibc) base exports to glibc targets,
    an Alpine (musl) base to musl/alpine targets.
    """

    venv: Annotated[dagger.Directory, Doc("The relocatable `.venv` directory.")] = field()
    python: Annotated[
        dagger.Directory,
        Doc("uv's managed-Python store (the interpreter the venv links against, plus uv's version symlinks)."),
    ] = field()
    python_path: Annotated[
        str,
        Doc("Absolute path the Python store must be mounted at for the venv to resolve its interpreter."),
    ] = field()

    @classmethod
    async def create(
        cls,
        container: Annotated[dagger.Container, Doc("Container holding the populated venv.")],
        venv_path: Annotated[str, Doc("Absolute path to the `.venv` within `container`.")],
    ) -> Self:
        """Build a `UvVenv` from a container holding a populated venv at `venv_path`.

        Reads the venv's ``pyvenv.cfg`` to locate uv's managed-Python store and
        exports the whole store alongside the venv. Raises unless the venv is
        relocatable and its Python is uv-managed (the only combination this can
        faithfully export).
        """
        cfg = parse_pyvenv_cfg(await container.file(posixpath.join(venv_path, "pyvenv.cfg")).contents())
        if cfg.get("relocatable", "").lower() != "true":
            msg = "UvVenv requires a relocatable venv; create it with with_venv(relocatable=True)."
            raise ValueError(msg)
        # `home` is the interpreter's bin/ dir: <store>/<impl-dir>/bin, where <store> is uv's
        # managed-Python store (default ~/.local/share/uv/python, or $UV_PYTHON_INSTALL_DIR) and
        # <impl-dir> is a python-build-standalone install, e.g. cpython-3.14-<plat>. uv names that
        # dir with the MINOR version and symlinks it to the real cpython-<patch>-<plat> dir; the
        # venv references the minor path by absolute symlink. Export the STORE (the parent of the
        # install dir, derived structurally — no hardcoded location, so $UV_PYTHON_INSTALL_DIR
        # works too) so the version symlink AND the real interpreter travel together and resolve
        # when remounted — and so we never extract a symlinked path directly (which
        # `Container.directory()` mishandles across engine versions; see the `bug` check).
        home = cfg.get("home", "").rstrip("/")
        install_dir = posixpath.dirname(home)  # <store>/<impl-dir>
        python_path = posixpath.dirname(install_dir)  # <store>
        if not posixpath.basename(install_dir).startswith(("cpython-", "pypy-", "graalpy-")):
            msg = (
                f"UvVenv only supports uv-managed (standalone) Pythons; the venv's interpreter dir "
                f"{install_dir!r} isn't a python-build-standalone install. Install one with "
                "with_python_install()."
            )
            raise ValueError(msg)
        return cls(
            venv=container.directory(venv_path),
            python=container.directory(python_path),
            python_path=python_path,
        )

    @function
    async def into(
        self,
        container: Annotated[dagger.Container, Doc("Container to add the venv and its Python to.")],
        path: Annotated[
            str,
            Doc(
                "Where to mount the venv; relative paths resolve against the container's workdir. Defaults to `.venv`."
            ),
        ] = ".venv",
        set_env_vars: Annotated[
            bool,
            Doc("Also set the standard activation env vars (`VIRTUAL_ENV` and a `PATH` with the venv's `bin/` first)."),
        ] = False,
    ) -> dagger.Container:
        """Mount the venv (at `path`) and its Python (at `python_path`) into `container`.

        With `set_env_vars`, exports `VIRTUAL_ENV` and prepends the venv's `bin/`
        to `PATH` so `python`/console scripts resolve without activation;
        otherwise the venv is just mounted (run it via `<path>/bin/python`).
        """
        workdir = await container.workdir()
        venv_path = path if posixpath.isabs(path) else posixpath.join(workdir, path)
        ctr = container.with_directory(self.python_path, self.python).with_directory(venv_path, self.venv)
        if set_env_vars:
            ctr = ctr.with_env_variable("VIRTUAL_ENV", venv_path).with_env_variable(
                "PATH", f"{venv_path}/bin:${{PATH}}", expand=True
            )
        return ctr
