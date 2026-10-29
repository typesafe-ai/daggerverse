---
icon: lucide/feather
title: flit_core
description: How the module builds workspace members that use flit_core.
---

# flit_core

Applies to packages with `build-backend = "flit_core.buildapi"`. The module reads:

- [`[tool.flit.module]`](https://flit.pypa.io/en/stable/pyproject_toml.html#module-section)
  `name`: the import name. A dotted name, such as `airflow.providers.smtp`, places the
  module inside namespace packages.
- A [dynamic](https://flit.pypa.io/en/stable/pyproject_toml.html#project-metadata)
  `version` or `description`: flit reads them from the module's `__init__.py`, which is
  available while the package builds.
- [`[tool.flit.external-data]`](https://flit.pypa.io/en/stable/pyproject_toml.html#external-data-section)
  `directory`.

The module sits under `src/` or at the package root. A `src/` layout is recognized by
an `__init__.py` in the module's top-level package (`src/airflow/__init__.py` for
`airflow.providers.smtp`); with a `src/` layout the whole `src/` directory is copied.

!!! warning "Not supported"
    - Single-file modules (`module.py` instead of `module/__init__.py`).
    - Namespace packages under `src/` whose top-level package has no `__init__.py`.
