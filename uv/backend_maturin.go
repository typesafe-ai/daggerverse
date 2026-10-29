package uv

// maturin: Rust extensions, either pure Rust or a Python package with a native submodule.
//
// See https://www.maturin.rs/project_layout and https://www.maturin.rs/config.

import (
	"slices"
	"strings"

	"dagger.io/dagger/core"

	"dagger/uv/internal/dagger"
)

// The package's own crate, relative to its `Cargo.toml`. Crates elsewhere in a
// Cargo workspace (a root `Cargo.toml`/`Cargo.lock`, path dependencies) are
// passed as `build_inputs` to the maturin handler.
var crateInputs = []string{
	"Cargo.toml",
	"Cargo.lock",
	"build.rs",
	"src/**",
	".cargo/config.toml",
	"rust-toolchain.toml",
	"rust-toolchain",
}

// `[project].dynamic` fields maturin fills from `Cargo.toml`, with the files they usually point at.
var cargoMetadataFiles = map[string][]string{"readme": {"README*"}, "license": {"LICENSE*", "COPYING*"}}

// Compiled crates are kept between builds; only the extension itself is installed.
// Concurrent builds get their own copy: maturin moves the built library out of the
// target directory after cargo releases its lock, so sharing one races.
const cargoTargetDir = "/mnt/cache/cargo-target"

// maturinSettings are the `[tool.maturin]` settings that determine the import layout and build inputs.
type maturinSettings struct {
	// `module-name`: the native module's import name. A dotted name places it in a
	// Python package (a mixed project); otherwise it is the whole package.
	module string
	// `python-source`: the directory holding the Python package of a mixed project.
	pythonSource *string
	// The directory of `manifest-path`, holding the crate's `Cargo.toml`.
	crateDir string
	// `include` entries that ship in wheels.
	includes []string
	// `[project].dynamic` fields maturin reads from `Cargo.toml`.
	cargoMetadata []string
}

func maturinFromPyproject(metadata map[string]any, name string) backendSettings {
	maturin := table(metadata, "tool", "maturin")
	includes := []string{}
	for _, entry := range anyList(maturin["include"]) {
		switch e := entry.(type) {
		case string:
			includes = append(includes, e)
		case map[string]any:
			format, ok := e["format"]
			if !ok {
				format = "wheel"
			}
			formats, isList := stringList(format)
			if format == "wheel" || (isList && slices.Equal(formats, []string{"wheel"})) {
				path, _ := e["path"].(string)
				includes = append(includes, path)
			}
		}
	}
	dynamic, _ := stringList(table(metadata, "project")["dynamic"])
	cargoMetadata := []string{}
	for field := range cargoMetadataFiles {
		if slices.Contains(dynamic, field) {
			cargoMetadata = append(cargoMetadata, field)
		}
	}
	slices.Sort(cargoMetadata)
	module, ok := maturin["module-name"].(string)
	if !ok {
		module = strings.ReplaceAll(strings.ToLower(name), "-", "_")
	}
	manifest, ok := maturin["manifest-path"].(string)
	if !ok {
		manifest = "Cargo.toml"
	}
	crateDir := dirname(normpath(manifest))
	if crateDir == "" {
		crateDir = "."
	}
	return maturinSettings{
		module:        module,
		pythonSource:  optionalString(maturin, "python-source"),
		crateDir:      crateDir,
		includes:      includes,
		cargoMetadata: cargoMetadata,
	}
}

// pkg is the Python package of a mixed project, or "" for pure Rust.
func (s maturinSettings) pkg() string {
	if s.pythonSource == nil && !strings.Contains(s.module, ".") {
		return ""
	}
	top, _, _ := strings.Cut(s.module, ".")
	return top
}

func (s maturinSettings) moduleName() *string { return nil }

func (s maturinSettings) modulePaths() []string {
	pkg := s.pkg()
	switch {
	case pkg == "":
		return []string{}
	case s.pythonSource != nil && *s.pythonSource != "":
		return []string{*s.pythonSource + "/" + pkg}
	}
	return []string{pkg}
}

func (s maturinSettings) sourcePaths() []string {
	pkg := s.pkg()
	switch {
	case pkg == "":
		return []string{}
	case s.pythonSource != nil && *s.pythonSource != "":
		return []string{*s.pythonSource}
	}
	return []string{pkg}
}

func (s maturinSettings) buildInputs() []string {
	inputs := []string{}
	for _, p := range crateInputs {
		inputs = append(inputs, normpath(pjoin(s.crateDir, p)))
	}
	if s.pkg() == "" {
		// A pure Rust module ships its type stubs from next to `pyproject.toml`.
		inputs = append(inputs, s.module+".pyi", "py.typed")
	}
	for _, field := range s.cargoMetadata {
		for _, p := range cargoMetadataFiles[field] {
			inputs = append(inputs, normpath(pjoin(s.crateDir, p)))
		}
	}
	for _, include := range s.includes {
		inputs = append(inputs, tree(include)...)
	}
	return inputs
}

// maturinHooks keep compiled crates in a cache volume while maturin builds.
type maturinHooks struct{}

func (maturinHooks) setup(container *dagger.Container) *dagger.Container {
	return container.WithMountedCache(
		cargoTargetDir, core.NewCacheVolume("cargo-target"), core.ContainerWithMountedCacheOpts{Sharing: core.CacheSharingModePrivate},
	).WithEnvVariable("CARGO_TARGET_DIR", cargoTargetDir)
}

func (maturinHooks) teardown(container *dagger.Container) *dagger.Container {
	return container.WithoutEnvVariable("CARGO_TARGET_DIR").WithoutMount(cargoTargetDir)
}
