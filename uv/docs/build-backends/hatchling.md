---
icon: lucide/egg
title: Hatchling
description: How the module builds workspace members that use Hatchling.
---

# Hatchling

Applies to packages with `build-backend = "hatchling.build"`. The module reads:

- [Explicit file selection](https://hatch.pypa.io/latest/config/build/#explicit-selection):
  `packages` and `only-include`. Only the selected paths are copied.
- [Forced inclusion](https://hatch.pypa.io/latest/config/build/#forced-inclusion):
  `force-include` sources.
- [Wheel options](https://hatch.pypa.io/latest/plugins/builder/wheel/#options):
  `bypass-selection`, `shared-data`, `shared-scripts`, and `extra-metadata`.
- [Custom build hook](https://hatch.pypa.io/latest/plugins/build-hook/custom/) and
  [custom metadata hook](https://hatch.pypa.io/latest/plugins/metadata-hook/custom/)
  scripts (`hatch_build.py` by default).
- The version file of the [`regex`](https://hatch.pypa.io/latest/plugins/version-source/regex/)
  and [`code`](https://hatch.pypa.io/latest/plugins/version-source/code/) version sources.

Options under `[tool.hatch.build.targets.wheel]` take precedence over the same options
under `[tool.hatch.build]`, as in Hatchling. Without an explicit selection, the module
expects the [conventional layout](index.md#unknown-build-backends).

For example, a `src/` layout with a metadata hook that renders the readme:

```toml
[tool.hatch.build.targets.wheel]
packages = ["src/openai"]

[tool.hatch.metadata.hooks.custom]
path = "scripts/hatch_metadata.py"
```

!!! warning "Not supported"
    - Version sources that read git, such as `hatch-vcs` or `uv-dynamic-versioning`.
    - `force-include` and shared-data sources outside the package directory
      (for example `../shared`).
    - Files a hook script reads on its own, other than the readme and license files.
