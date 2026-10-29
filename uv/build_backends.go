package uv

// Build backends: each one's import layout and the files it reads while building.
//
// The module scaffolds local packages as stubs before their real source arrives,
// so it must know where each backend expects the import directories and which
// extra files (readme, license files, version source, hook scripts) the build reads.
//
// Each supported backend lives in its own file and is registered in
// `backends` under its `[build-system].build-backend` value. Its settings are plain
// Go types derived from a package's `pyproject.toml` on demand. Handlers
// (see handler.go) configure a built-in backend for some packages, or support a
// backend this module doesn't know, through the `BuildBackendHandler` interface.

import (
	"slices"
	"strings"

	"dagger/uv/internal/dagger"
)

// backendSettings are a backend's settings that shape a package's staging.
//
// nil from `moduleName`, `modulePaths`, or `sourcePaths` falls back to the
// generic layout: the module named after the distribution, under `src/` or at
// the package root.
type backendSettings interface {
	// The import name for the generic layout, if the backend declares one.
	moduleName() *string
	// Declared import directories (or `.py` modules).
	modulePaths() []string
	// Declared directories (or files) to copy.
	sourcePaths() []string
	// Files (or globs) the backend reads while building.
	//
	// Relative to the package, or to the workspace root with a leading `/`.
	buildInputs() []string
}

// backendHooks run around the local packages' build: a handler, or a built-in backend that needs it.
type backendHooks interface {
	// Prepare the container before the local packages build, e.g. mount caches or set variables.
	setup(container *dagger.Container) *dagger.Container
	// Undo what `setup` shouldn't leave in the final container, e.g. variables or secrets.
	teardown(container *dagger.Container) *dagger.Container
}

// builtin is a backend this module supports.
type builtin struct {
	// Its settings, parsed from a package's metadata and distribution name.
	parse func(metadata map[string]any, name string) backendSettings
	// What runs around the build, if anything.
	hooks backendHooks
}

var backends = map[string]builtin{
	"uv_build":                {uvBuildFromPyproject, nil},
	"hatchling.build":         {hatchlingFromPyproject, nil},
	"pdm.backend":             {pdmFromPyproject, nil},
	"flit_core.buildapi":      {flitFromPyproject, nil},
	"poetry.core.masonry.api": {poetryFromPyproject, nil},
	"maturin":                 {maturinFromPyproject, maturinHooks{}},
}

// packageBackend is a package's build backend together with the settings that shape its staging.
type packageBackend struct {
	// `[build-system].build-backend`, if declared.
	name *string
	// Settings of a supported backend, or a handler's answers; nil for any other backend.
	settings backendSettings
	// `[project]` readme and license files, which every backend reads.
	projectInputs []string
}

// packageBackendFromPyproject is the package's backend; `settings` (resolved from a handler) replaces the built-in ones.
func packageBackendFromPyproject(metadata map[string]any, name string, settings backendSettings) packageBackend {
	backend := optionalString(table(metadata, "build-system"), "build-backend")
	if settings == nil && backend != nil {
		if builtin, ok := backends[*backend]; ok {
			settings = builtin.parse(metadata, name)
		}
	}
	return packageBackend{
		name:          backend,
		settings:      settings,
		projectInputs: projectInputs(table(metadata, "project")),
	}
}

// moduleName is the backend-declared import name for the generic layout, if any.
func (b packageBackend) moduleName() *string {
	if b.settings == nil {
		return nil
	}
	return b.settings.moduleName()
}

// modulePaths are declared import directories (or `.py` modules), or nil to fall back to the generic layout.
func (b packageBackend) modulePaths() []string {
	if b.settings == nil {
		return nil
	}
	return b.settings.modulePaths()
}

// sourcePaths are declared directories (or files) to copy, or nil to fall back to the generic layout.
func (b packageBackend) sourcePaths() []string {
	if b.settings == nil {
		return nil
	}
	return b.settings.sourcePaths()
}

// buildInputs are package-relative files (or globs) the backend reads while building, staged into the scaffold.
func (b packageBackend) buildInputs() []string {
	var backendInputs []string
	if b.settings != nil {
		backendInputs = b.settings.buildInputs()
	}
	return unique(slices.Concat(b.projectInputs, backendInputs))
}

// projectInputs are files named by PEP 621 metadata: `license-files`, a readme file, and a legacy `license.file`.
func projectInputs(project map[string]any) []string {
	paths, _ := stringList(project["license-files"])
	paths = slices.Clone(paths)
	readme := project["readme"]
	if t, ok := readme.(map[string]any); ok {
		readme = t["file"]
	}
	if s, ok := readme.(string); ok {
		paths = append(paths, s)
	}
	if license, ok := project["license"].(map[string]any); ok {
		if file, ok := license["file"]; ok {
			s, _ := file.(string)
			paths = append(paths, s)
		}
	}
	return paths
}

// unique drops repeated values, keeping the first occurrence (Python's `dict.fromkeys`).
func unique(values []string) []string {
	result := []string{}
	for _, v := range values {
		if !slices.Contains(result, v) {
			result = append(result, v)
		}
	}
	return result
}

// Path classification shared by the backend settings.
//
// Backends list files and directories in the same options without saying which
// is which, and the settings are resolved from metadata alone (no source
// listing), so a path with an extension is treated as a file.

func isFile(p string) bool {
	base := strings.TrimLeft(basename(p), ".")
	return strings.Contains(base, ".")
}

// isModule reports a package directory or a single-file `.py` module.
func isModule(p string) bool {
	return !isFile(p) || strings.HasSuffix(p, ".py")
}

// tree returns include patterns matching `p` whether it is a file or a directory.
func tree(p string) []string {
	if isFile(p) {
		return []string{p}
	}
	return []string{p, p + "/**"}
}

// withinPackage returns the normalized source keys of a `source -> destination` mapping that stay inside the package.
//
// Sources outside the package directory (absolute or `../`) can't be staged
// from the package's own tree and are left out.
func withinPackage(sources []string) []string {
	result := []string{}
	for _, source := range sources {
		source = normpath(source)
		if !strings.HasPrefix(source, "/") && !strings.HasPrefix(source, "../") && source != ".." {
			result = append(result, source)
		}
	}
	return result
}

func normpaths(paths []string) []string {
	result := make([]string, len(paths))
	for i, p := range paths {
		result[i] = normpath(p)
	}
	return result
}

func filter(values []string, keep func(string) bool) []string {
	result := []string{}
	for _, v := range values {
		if keep(v) {
			result = append(result, v)
		}
	}
	return result
}
