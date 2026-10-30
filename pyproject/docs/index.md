---
icon: lucide/rocket
title: Overview
description: A Dagger module that discovers Python projects and workspaces and loads exactly the source each one needs.
---

# pyproject

A [Dagger](https://dagger.io) module that discovers the Python projects and
workspaces in a Dagger workspace, and loads only the files each one needs.
It runs no tools itself: it is the discovery layer for modules that do.

It exposes two [collections](https://docs.dagger.io/reference/sdks/python/#collections),
both keyed by path relative to the workspace root (`.` for the root):

- `projects`: every directory holding a `pyproject.toml`.
- `workspaces`: every directory holding a workspace marker, `uv.lock` or
  `pylock.toml` by default. A workspace is an environment root: it installs its
  members and their local dependencies.

## Installation

```console
$ dagger install github.com/typesafe-ai/daggerverse/pyproject
```

## Usage

=== "Shell"

    ```console
    $ dagger -c 'pyproject | workspaces | keys'
    $ dagger -c 'pyproject | workspaces | get services/api | members'
    $ dagger -c 'pyproject | projects | get services/api | source | entries'
    ```

=== "Python SDK"

    ```python
    from dagger_clients.pyproject import pyproject

    found = pyproject(ws, exclude=["**/tests/fixtures/**"])
    for workspace in await found.workspaces().list_():
        deps = workspace.manifests()  # resolver inputs only
        src = workspace.source()  # members and their local dependencies
    ```

### Projects

| Field | Meaning |
|-------|---------|
| `path` | Project root. |
| `name` | `[project].name`, or null. |
| `generated` | Whether Dagger generated this project (`[tool.dagger] generated`), such as a module's `sdk/` or `clients/*`. Tools that check user code should skip it. |
| `workspace` | Root of the workspace that installs this project as a member, or null. |
| `nested` | Topmost projects nested inside this one. Their files are their own. |
| `local_dependencies` | Projects this one depends on through `[tool.uv.sources]` path and workspace sources, transitively, wherever they live. |

`source()` returns the project's own files plus its local dependencies'. Paths
stay relative to the workspace root, so a path source like `../lib` resolves
once the directory is mounted as the root. `files()` returns only the files
the project owns, its tree less nested projects, for tools such as linters that
work on one project's own code. `pyproject()` returns the manifest.

### Workspaces

| Field | Meaning |
|-------|---------|
| `path` | Workspace root. |
| `lockfiles` | Markers found at the root, e.g. `uv.lock`. |
| `members` | Member projects, the root project first. |
| `local_dependencies` | Projects the members depend on through path sources, outside the membership. |

A uv workspace's members are the root plus every project its
`[tool.uv.workspace]` `members` globs select and `exclude` doesn't, as uv
decides. Any other workspace has its root project alone, or none for a bare
lockfile.

- `projects()` is the collection of the members.
- `source()` returns every member's files and their local dependencies'.
- `manifests()` returns only what dependency resolution reads: the markers,
  `uv.toml`, and each member's and local dependency's `pyproject.toml` and
  `.python-version`. Build a dependency layer from it (e.g. `uv sync --frozen
  --no-install-local`) and source edits leave that layer cached.

## How sources are scoped

Discovery skips gitignored directories, so a gitignored copy of the repository
(such as an agent's worktree) adds no projects. Every file belongs to the deepest project containing it. A `source` load
includes the project trees it needs and leaves out any other project nested
inside them. A monorepo root's source carries only the root's own files, and
a workspace's source leaves out test fixtures nested in a member. Caches,
virtualenvs, build output, VCS metadata and gitignored files are always left out.

Path sources that point at missing directories are kept and load nothing, so
discovery still works on a partial checkout. Path sources above the workspace
root can't be loaded and are skipped.

## Settings

```toml title="dagger.toml"
[modules.pyproject.settings]
# Glob patterns of project and workspace roots to leave out of both collections.
exclude = ["**/tests/_packages/**"]
# File names that make a directory a workspace.
workspaceMarkers = ["uv.lock", "pylock.toml", "pylock.prod.toml"]
```

An excluded project is left out of the collections only. Its files still
belong to it rather than to the project around it, and it still counts as a
dependency. `**` crosses directories,
`*` doesn't, and a trailing `/**` also matches the directory itself.

Markers are exact file names. List each named PEP 751 lock, such as
`pylock.prod.toml`, that should make a workspace.

## Limitations

- Only `[tool.uv.sources]` path and workspace sources are followed. A
  `name @ file://…` direct reference is not.
- A malformed `pyproject.toml` fails discovery when a listed project or a
  dependency needs it. Exclude it to skip it.

## Where to go next

- [SDK reference](https://daggerverse.dev/mod/github.com/typesafe-ai/daggerverse/pyproject)
