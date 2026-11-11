"""Path classification shared by the backend settings.

Backends list files and directories in the same options without saying which
is which, and the settings are resolved from metadata alone (no source
listing), so a path with an extension is treated as a file.
"""

import posixpath


def is_file(path: str) -> bool:
    return bool(posixpath.splitext(posixpath.basename(path))[1])


def is_module(path: str) -> bool:
    """A package directory or a single-file `.py` module."""
    return not is_file(path) or path.endswith(".py")


def tree(path: str) -> list[str]:
    """Include patterns matching `path` whether it is a file or a directory."""
    return [path] if is_file(path) else [path, f"{path}/**"]


def in_workspace(mapping: dict[str, str]) -> list[str]:
    """Normalized source keys of a `source -> destination` mapping that the workspace can hold.

    Absolute sources are outside it and are left out. A source above the
    package (`../`) is staged from the workspace root, see `rooted`.
    """
    sources = (posixpath.normpath(source) for source in mapping)
    return [source for source in sources if not source.startswith("/")]


def rooted(package_root: str, path: str) -> str:
    """The package-relative build input `path`, `/`-prefixed and workspace-root-relative if it leaves the package.

    A backend reads such a file from above the package, e.g. hatch's
    `version.path = "../VERSION"`. The package's own tree doesn't hold it, so
    it is staged from the workspace root, as `/`-prefixed inputs are.
    `package_root` is relative to the workspace root.
    """
    if path.startswith("/") or not (posixpath.normpath(path) + "/").startswith("../"):
        return path
    resolved = posixpath.normpath(posixpath.join(package_root, path))
    if (resolved + "/").startswith("../"):
        msg = f"build input {path!r} of the package at {package_root!r} is outside the workspace"
        raise ValueError(msg)
    return f"/{resolved}"
