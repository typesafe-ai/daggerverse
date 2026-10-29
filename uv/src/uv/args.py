from typing import Annotated, TypeAlias

from dagger import Doc

WorkspacePath: TypeAlias = Annotated[
    str,
    Doc("Path to the uv workspace root (holding uv.lock and pyproject.toml), relative to the workspace root."),
]

Package: TypeAlias = Annotated[
    list[str],
    Doc(
        "Package names to install; passed to `uv sync` as repeated `--package`. "
        "Defaults to the current (workspace root) package, mirroring a bare `uv sync`. "
        "Use `all_packages` to install every workspace member instead."
    ),
]

Extra: TypeAlias = Annotated[
    list[str],
    Doc("Extras to install; passed to `uv sync` as repeated `--extra`"),
]

Group: TypeAlias = Annotated[
    list[str],
    Doc("Dependency groups to install; passed to `uv sync` as repeated `--group`"),
]

AllExtras: TypeAlias = Annotated[
    bool,
    Doc("Install every extra; maps to `uv sync --all-extras`"),
]

AllGroups: TypeAlias = Annotated[
    bool,
    Doc("Install every dependency group; maps to `uv sync --all-groups`"),
]

NoDefaultGroups: TypeAlias = Annotated[
    bool,
    Doc(
        "Skip the default dependency groups (`[tool.uv] default-groups`, `dev` unless set); "
        "maps to `uv sync --no-default-groups`. Groups passed with `group` are still installed."
    ),
]

AllPackages: TypeAlias = Annotated[
    bool,
    Doc("Install every workspace member; maps to `uv sync --all-packages`"),
]

PruneCache: TypeAlias = Annotated[
    bool,
    Doc(
        "Whether to run `uv cache prune --ci` after installing remote dependencies. Learn more about the reasoning in [uv docs](https://docs.astral.sh/uv/concepts/cache/#caching-in-continuous-integration)."
    ),
]

MaxCacheSize: TypeAlias = Annotated[
    int,
    Doc("Prune the uv cache only once it grows past this many GiB."),
]

LockTimeout: TypeAlias = Annotated[
    int,
    Doc("Seconds uv waits to acquire the cache file lock (`UV_LOCK_TIMEOUT`) while pruning the cache."),
]

NoEditable: TypeAlias = Annotated[
    bool,
    Doc(
        "Install local/workspace packages non-editable (`uv sync --no-editable`), "
        "baking their source into site-packages instead of linking it. Makes the "
        "resulting venv self-contained, so it can be exported/copied (e.g. via "
        "`copy_venv`) without also carrying the workspace source."
    ),
]

VenvRelocatable: TypeAlias = Annotated[
    bool,
    Doc(
        "Create a relocatable `.venv` (`uv venv --relocatable`) before installing, even when the base "
        "sets `UV_PROJECT_ENVIRONMENT`. Needed to copy the environment into another image (see `venv`)."
    ),
]

ActivateVenv: TypeAlias = Annotated[
    bool,
    Doc(
        "When the build uses a `.venv`, activate it in the container: set `VIRTUAL_ENV` and put its "
        "`bin/` first on `PATH`, so a plain `python` runs the installed environment."
    ),
]
