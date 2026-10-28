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


def within_package(mapping: dict[str, str]) -> list[str]:
    """Normalized source keys of a `source -> destination` mapping that stay inside the package.

    Sources outside the package directory (absolute or `../`) can't be staged
    from the package's own tree and are left out.
    """
    sources = (posixpath.normpath(source) for source in mapping)
    return [source for source in sources if not source.startswith(("/", "../")) and source != ".."]
