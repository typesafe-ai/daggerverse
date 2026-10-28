---
icon: lucide/package-open
title: pdm-backend
description: How the module builds workspace members that use pdm-backend.
---

# pdm-backend

Applies to packages with `build-backend = "pdm.backend"`. The module reads these
`[tool.pdm]` settings:

- [`includes`](https://backend.pdm-project.org/build_config/#include-or-exclude-files):
  the listed paths are copied. Glob entries, such as `pkg/**/*.json`, select data inside
  the packages and don't change what is copied.
- [`package-dir`](https://backend.pdm-project.org/build_config/#the-src-layout): the
  whole source root is copied.
- [`custom-hook`](https://backend.pdm-project.org/build_config/#local-build-hooks): the
  [build hook](https://backend.pdm-project.org/hooks/) script (`pdm_build.py` by
  default).
- [`[tool.pdm.version]` with `source = "file"`](https://backend.pdm-project.org/metadata/#read-from-a-static-string-in-the-given-file-path):
  the version file.

Without `includes` or `package-dir`, the module expects the
[conventional layout](index.md#other-backends).

!!! warning "Not supported"
    - [`source = "scm"`](https://backend.pdm-project.org/metadata/#read-from-scm-tag-supporting-git-and-hg)
      versions, which read git.
    - [`source = "call"`](https://backend.pdm-project.org/metadata/#get-with-a-specific-function)
      versions in editable installs: the getter is imported before the package's
      source is copied.
    - Files a hook script reads on its own, other than the readme and license files.
