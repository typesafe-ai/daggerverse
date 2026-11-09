---
icon: lucide/history
---

# Changelog

## 0.3.1 - 2026-11-09

### Bug fixes

- don't fail the whole audit generator when one package can't be upgraded ([#185](https://github.com/typesafe-ai/daggerverse/pull/185))

## 0.3.0 - 2026-11-06

The module has been upgraded to Dagger 1.0. It's builder API has been [redesigned](https://daggerverse.docs.typesafe.ai/uv/building/) accordingly. This naturally comes with a lot of performance improvements.

Many popular Python build backends (such as `hatchling`, `poetry-core`, and `maturin`) are now [supported](https://daggerverse.docs.typesafe.ai/uv/build-backends/), and it's easy to plug in a custom handler for your own build backend.

### Bug fixes

- install without a redundant .venv by default and activate it otherwise ([#138](https://github.com/typesafe-ai/daggerverse/pull/138))
- rebuild editable packages whose backend writes into the source tree ([#147](https://github.com/typesafe-ai/daggerverse/pull/147))
- build local packages from the current source over kept caches ([#176](https://github.com/typesafe-ai/daggerverse/pull/176))

### Features

- use the Workspace API ([#118](https://github.com/typesafe-ai/daggerverse/pull/118))
- support hatchling and pdb build backends, test builds on popular real-world projects ([#130](https://github.com/typesafe-ai/daggerverse/pull/130))
- support flit build backend ([#131](https://github.com/typesafe-ai/daggerverse/pull/131))
- support poetry-core build backend ([#132](https://github.com/typesafe-ai/daggerverse/pull/132))
- support maturin build backend ([#141](https://github.com/typesafe-ai/daggerverse/pull/141))
- support namespace and type-stub packages ([#144](https://github.com/typesafe-ai/daggerverse/pull/144))
- support pluggable build backends ([#137](https://github.com/typesafe-ai/daggerverse/pull/137))
- support custom build backend hooks ([#140](https://github.com/typesafe-ai/daggerverse/pull/140))
- make `audit` check a generator that can auto-upgrade vulnerable dependencies  ([#143](https://github.com/typesafe-ai/daggerverse/pull/143))

### Performance

- read only what each step needs from the workspace ([#139](https://github.com/typesafe-ai/daggerverse/pull/139))
- read discovered projects and workspaces in one call ([#154](https://github.com/typesafe-ai/daggerverse/pull/154))
- discover from a workspace of just the files discovery reads ([#155](https://github.com/typesafe-ai/daggerverse/pull/155))
- load discovery inputs in one engine call ([#163](https://github.com/typesafe-ai/daggerverse/pull/163))

## 0.2.5 - 2026-09-25

### Bug fixes

- support virtual members and more `uv_build` build backend configuration options ([#110](https://github.com/typesafe-ai/daggerverse/pull/110))

### Features

- add deptry module ([#91](https://github.com/typesafe-ai/daggerverse/pull/91))
- auto-include license files into build context ([#96](https://github.com/typesafe-ai/daggerverse/pull/96))

### Miscellaneous

- ignore other filesystems when calculating cache size ([#90](https://github.com/typesafe-ai/daggerverse/pull/90))
- enable deptry check ([#92](https://github.com/typesafe-ai/daggerverse/pull/92))
## 0.2.4 - 2026-08-20

release v0.2.4

### Features

- only prune `uv` cache when growing over certain size ([#89](https://github.com/typesafe-ai/daggerverse/pull/89))
## 0.2.3 - 2026-08-13

release uv v0.2.1

### Bug fixes

- actually pick up `ruff` version from `uv.lock` ([#85](https://github.com/typesafe-ai/daggerverse/pull/85))
- batch workspace source layers ([#86](https://github.com/typesafe-ai/daggerverse/pull/86))
- always mount uv-cache volume ([#88](https://github.com/typesafe-ai/daggerverse/pull/88))

### Features

- enable uv cache pruning (CI mode) by default ([#87](https://github.com/typesafe-ai/daggerverse/pull/87))
## 0.2.1 - 2026-06-17

### Bug fixes

- better UV_CACHE_DIR handling ([#80](https://github.com/typesafe-ai/daggerverse/pull/80))
- skip dagger codegen if no dagger-io dep is present ([#81](https://github.com/typesafe-ai/daggerverse/pull/81))

## 0.2.0 - 2026-06-16

This release ships a change that may be breaking when building nested standalone packages - the resulting layout inside the container now replicates the original layout in the source directory passed to the module.

### Bug fixes

- place standalone (non-workspace) packages at the correct location in the container ([#79](https://github.com/typesafe-ai/daggerverse/pull/79))

### Documentation

- remove custom homepage link ([#71](https://github.com/typesafe-ai/daggerverse/pull/71))

### Features

- add `UvWorkspaceBuild.with_uv` that can be used to install `uv` into the base container ([#75](https://github.com/typesafe-ai/daggerverse/pull/75))
- add `uv` auto-installation ([#76](https://github.com/typesafe-ai/daggerverse/pull/76))

## 0.1.3 - 2026-06-10

### Documentation

- link official daggerverse docs ([#65](https://github.com/typesafe-ai/daggerverse/pull/65))
- remove double backticks ([#66](https://github.com/typesafe-ai/daggerverse/pull/66))

### Features

- merge indices discovered at workspace and member level ([#70](https://github.com/typesafe-ai/daggerverse/pull/70))

### Miscellaneous

- remove legacy `uv-workspace` module ([#67](https://github.com/typesafe-ai/daggerverse/pull/67))

## 0.1.0 - 2026-06-09

### Bug fixes

- avoid PyPI calls when handling `required-version` ([#47](https://github.com/typesafe-ai/daggerverse/pull/47))
- handle and document editable dependencies when building a `.venv` ([#52](https://github.com/typesafe-ai/daggerverse/pull/52))

### Documentation

- deploy to github pages ([#54](https://github.com/typesafe-ai/daggerverse/pull/54))
- add zensical metadata ([#55](https://github.com/typesafe-ai/daggerverse/pull/55))
- add home url ([#59](https://github.com/typesafe-ai/daggerverse/pull/59))
- improve docs ([#62](https://github.com/typesafe-ai/daggerverse/pull/62))
- add docs link to module docs ([#64](https://github.com/typesafe-ai/daggerverse/pull/64))

### Features

- uv module ([#44](https://github.com/typesafe-ai/daggerverse/pull/44))
- better audit error traces ([#49](https://github.com/typesafe-ai/daggerverse/pull/49))
- add installation capabilities ([#50](https://github.com/typesafe-ai/daggerverse/pull/50))
- add `UvWorkspaceSource.indices` function ([#61](https://github.com/typesafe-ai/daggerverse/pull/61))

### Miscellaneous

- reformat to 120 line length ([#46](https://github.com/typesafe-ai/daggerverse/pull/46))
- finalize module description ([#63](https://github.com/typesafe-ai/daggerverse/pull/63))
