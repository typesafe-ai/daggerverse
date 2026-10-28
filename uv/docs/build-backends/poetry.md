---
icon: lucide/scroll-text
title: poetry-core
description: How the module builds workspace members that use poetry-core.
---

# poetry-core

Applies to packages with `build-backend = "poetry.core.masonry.api"`. The module reads
these `[tool.poetry]` settings:

- [`packages`](https://python-poetry.org/docs/pyproject/#packages): each entry's
  `include` under `from` is copied. Entries limited to `format = "sdist"` are skipped,
  and a glob entry such as `extra/**/*.py` copies its leading directory.
- [`include`](https://python-poetry.org/docs/pyproject/#exclude-and-include): files and
  globs that ship in wheels are available while the package builds.
- [`readme`](https://python-poetry.org/docs/pyproject/#readme-1): the legacy readme
  file(s), for projects without a `[project]` table.
- `[tool.poetry.build]` `script` (or the older `build = "build.py"`): the build script.

Without `packages`, the module expects the
[conventional layout](index.md#other-backends).

!!! warning "Not supported"
    - Versions from plugins that read git, such as `poetry-dynamic-versioning`.
    - Files a build script reads on its own, other than the readme and license files.
