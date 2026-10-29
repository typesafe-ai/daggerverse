---
icon: lucide/cog
title: maturin
description: How the module builds workspace members that use maturin.
---

# maturin

Applies to packages with `build-backend = "maturin"`. The module reads these
[`[tool.maturin]`](https://www.maturin.rs/config) settings:

- `module-name`: the native module's import name. A dotted name, such as
  `watchfiles._rust_notify`, makes a [mixed project](https://www.maturin.rs/project_layout):
  a Python package with a native submodule. Otherwise the project is pure Rust and
  only its `<module-name>.pyi` stub and `py.typed` are staged.
- `python-source`: the directory holding a mixed project's Python package.
- `manifest-path`: where the crate's `Cargo.toml` is.
- `include`: files that ship in wheels.

The crate's `Cargo.toml`, `Cargo.lock`, `build.rs`, `src/`, `.cargo/config.toml` and
`rust-toolchain` are available while the package builds, and so are the readme and
license files when `[project].dynamic` takes them from `Cargo.toml`. Compiled crates
are kept in a cache volume between builds.

Cargo never prunes that cache, so it's cleared after a build that leaves it larger
than 50 GiB. Change the limit with the maturin handler's `with_max_target_size`
(`0` clears it after every build).

Editable installs of a mixed project rebuild it every time they run: maturin
writes the extension into the project's Python package rather than into the wheel
uv caches, so a cached build would install without it. The compiled crates are
reused, so the rebuild is incremental. A pure Rust module's editable wheel carries
the extension itself, so it's installed from uv's cache as usual.

The base container needs a Rust toolchain.

## Cargo workspaces

Crates outside the package, such as a Cargo workspace's `Cargo.toml` and `Cargo.lock`
or path dependencies, aren't staged automatically. Add them per package with the
maturin handler's `with_package_config`, as workspace-root-relative
`extra_build_inputs`, and pass it to `build()`, `install()` or `venv()`. They may
lie outside the uv workspace, e.g. a crate shared with a sibling project:

```python
uv_ = uv(ws)
maturin = (
    uv_.build_backend_handlers()
    .maturin()
    .with_package_config(
        name="jiter",
        extra_build_inputs=["/Cargo.toml", "/Cargo.lock", "/crates/jiter/Cargo.toml", "/crates/jiter/src/**"],
    )
)
ctr = uv_.workspace(".").install(build_backend_handlers=[maturin])
```

Workspace-root-relative inputs are read from the `ws` argument, which defaults to
the current workspace, filtered to just those files. When you gave `uv()` a
different workspace, such as `directory.as_workspace()`, pass the same one as `ws`.

!!! warning "Declared targets"
    Cargo loads every workspace member's declared targets, so list their files too,
    such as a sibling crate's `build.rs`, `tests/*.rs` and `benches/*.rs`.
