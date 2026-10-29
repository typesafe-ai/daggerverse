package uv

// Build backend handlers: Dagger objects that tell the module how a backend builds a package.
//
// A handler answers, from a package's `pyproject.toml`, the same questions as a
// built-in backend's settings, and can wrap the build in `setup`/`teardown`. The
// module provides a handler type for each of its built-in backends
// (`BuildBackendHandlers`), to configure how some packages build; other modules
// implement the `BuildBackendHandler` interface to support any other backend. A handler's
// answers are resolved once per package while the sync plan is created and stored
// as a `BuildBackendLayout`, so the rest of the pipeline reads them like built-in
// settings.

import (
	"context"
	"encoding/json"

	"golang.org/x/sync/errgroup"

	"dagger/uv/internal/dagger"
)

// Tells the module how a build backend builds the packages that use it.
//
// Every function except `build_backend`, `setup` and `teardown` receives the
// package's `pyproject.toml` contents and its distribution name.
// Returning an empty `module_name` falls back to the generic layout: the
// module named after the distribution, under `src/` or at the package root.
// Empty `module_paths` or `source_paths` fall back the same way only where the
// module's built-in support for the package's backend would.
type BuildBackendHandler interface {
	DaggerObject
	// The `[build-system].build-backend` value this handler handles, e.g. `setuptools.build_meta`.
	BuildBackend(ctx context.Context) (string, error)
	// The import name for the generic layout, dotted for a module inside namespace packages.
	ModuleName(ctx context.Context, pyproject string, name string) (string, error)
	// Package-relative import directories (or `.py` modules) to scaffold.
	ModulePaths(ctx context.Context, pyproject string, name string) ([]string, error)
	// Package-relative directories (or files) to copy as the package's source.
	SourcePaths(ctx context.Context, pyproject string, name string) ([]string, error)
	// Files (or globs) the backend reads while building: package-relative, or workspace-root-relative with `/`.
	BuildInputs(ctx context.Context, pyproject string, name string) ([]string, error)
	// Prepare the container before the local packages build, e.g. mount caches, set variables or secrets.
	//
	// Runs once per build, before the `uv sync` that builds every local package
	// this handler handles. Return `container` unchanged when nothing is needed.
	Setup(
		// An OCI-compatible container, also known as a Docker container.
		container *dagger.Container,
	) *dagger.Container
	// Remove what `setup` added that shouldn't stay in the final container, e.g. variables or secrets.
	//
	// Runs right after that `uv sync`. Return `container` unchanged when nothing is needed.
	Teardown(
		// An OCI-compatible container, also known as a Docker container.
		container *dagger.Container,
	) *dagger.Container
}

// BuildBackendHandlerList is the `build_backend_handlers` argument. The Go SDK
// can't decode a list of interfaces inside an inline argument struct, so the
// list decodes its handlers itself, from their IDs.
type BuildBackendHandlerList []BuildBackendHandler

func (l *BuildBackendHandlerList) UnmarshalJSON(data []byte) error {
	var ids []json.RawMessage
	if err := json.Unmarshal(data, &ids); err != nil {
		return err
	}
	*l = make(BuildBackendHandlerList, len(ids))
	for i, id := range ids {
		impl := &buildBackendHandlerImpl{}
		if err := json.Unmarshal(id, impl); err != nil {
			return err
		}
		(*l)[i] = impl
	}
	return nil
}

// A package layout resolved from a `BuildBackendHandler`.
type BuildBackendLayout struct {
	// Import name for the generic layout, if declared
	ModuleName *string
	// Import directories (or `.py` modules) to scaffold
	ModulePaths []string
	// Directories (or files) to copy
	SourcePaths []string
	// Files (or globs) the backend reads while building
	BuildInputs []string
}

func resolveBuildBackendLayout(ctx context.Context, handler BuildBackendHandler, pyproject, name string) (*BuildBackendLayout, error) {
	layout := &BuildBackendLayout{}
	eg, ctx := errgroup.WithContext(ctx)
	eg.Go(func() error {
		moduleName, err := handler.ModuleName(ctx, pyproject, name)
		if moduleName != "" {
			layout.ModuleName = &moduleName
		}
		return err
	})
	eg.Go(func() (err error) {
		layout.ModulePaths, err = handler.ModulePaths(ctx, pyproject, name)
		return err
	})
	eg.Go(func() (err error) {
		layout.SourcePaths, err = handler.SourcePaths(ctx, pyproject, name)
		return err
	})
	eg.Go(func() (err error) {
		layout.BuildInputs, err = handler.BuildInputs(ctx, pyproject, name)
		return err
	})
	return layout, eg.Wait()
}

func (l *BuildBackendLayout) moduleName() *string   { return l.ModuleName }
func (l *BuildBackendLayout) modulePaths() []string { return l.ModulePaths }
func (l *BuildBackendLayout) sourcePaths() []string { return l.SourcePaths }
func (l *BuildBackendLayout) buildInputs() []string { return l.BuildInputs }

// handlerHooks runs a handler's `setup`/`teardown` around the build.
type handlerHooks struct{ handler BuildBackendHandler }

func (h handlerHooks) setup(container *dagger.Container) *dagger.Container {
	return h.handler.Setup(container)
}

func (h handlerHooks) teardown(container *dagger.Container) *dagger.Container {
	return h.handler.Teardown(container)
}

// Built-in handlers: the handler interface for a built-in backend, answering like
// the module's own support for it. A backend with options adds them to its own
// handler type, e.g. `MaturinHandler`'s `WithPackageConfig`.

func builtinSettings(backend, pyproject, name string) (backendSettings, error) {
	metadata, err := loadsToml(pyproject)
	if err != nil {
		return nil, err
	}
	return backends[backend].parse(metadata, name), nil
}

func builtinModuleName(backend, pyproject, name string) (string, error) {
	settings, err := builtinSettings(backend, pyproject, name)
	if err != nil {
		return "", err
	}
	if moduleName := settings.moduleName(); moduleName != nil {
		return *moduleName, nil
	}
	return "", nil
}

func builtinModulePaths(backend, pyproject, name string) ([]string, error) {
	settings, err := builtinSettings(backend, pyproject, name)
	if err != nil {
		return nil, err
	}
	return settings.modulePaths(), nil
}

func builtinSourcePaths(backend, pyproject, name string) ([]string, error) {
	settings, err := builtinSettings(backend, pyproject, name)
	if err != nil {
		return nil, err
	}
	return settings.sourcePaths(), nil
}

func builtinBuildInputs(backend, pyproject, name string) ([]string, error) {
	settings, err := builtinSettings(backend, pyproject, name)
	if err != nil {
		return nil, err
	}
	return settings.buildInputs(), nil
}

func builtinSetup(backend string, container *dagger.Container) *dagger.Container {
	if hooks := backends[backend].hooks; hooks != nil {
		return hooks.setup(container)
	}
	return container
}

func builtinTeardown(backend string, container *dagger.Container) *dagger.Container {
	if hooks := backends[backend].hooks; hooks != nil {
		return hooks.teardown(container)
	}
	return container
}

// Handles packages built with `uv_build`.
type UvBuildHandler struct{}

const uvBuildBackend = "uv_build"

// The `[build-system].build-backend` value this handler handles.
func (h *UvBuildHandler) BuildBackend() string { return uvBuildBackend }

func (h *UvBuildHandler) ModuleName(pyproject string, name string) (string, error) {
	return builtinModuleName(uvBuildBackend, pyproject, name)
}

func (h *UvBuildHandler) ModulePaths(pyproject string, name string) ([]string, error) {
	return builtinModulePaths(uvBuildBackend, pyproject, name)
}

func (h *UvBuildHandler) SourcePaths(pyproject string, name string) ([]string, error) {
	return builtinSourcePaths(uvBuildBackend, pyproject, name)
}

func (h *UvBuildHandler) BuildInputs(pyproject string, name string) ([]string, error) {
	return builtinBuildInputs(uvBuildBackend, pyproject, name)
}

func (h *UvBuildHandler) Setup(
	// An OCI-compatible container, also known as a Docker container.
	container *dagger.Container,
) *dagger.Container {
	return builtinSetup(uvBuildBackend, container)
}

func (h *UvBuildHandler) Teardown(
	// An OCI-compatible container, also known as a Docker container.
	container *dagger.Container,
) *dagger.Container {
	return builtinTeardown(uvBuildBackend, container)
}

// Handles packages built with Hatchling.
type HatchlingHandler struct{}

const hatchlingBackend = "hatchling.build"

// The `[build-system].build-backend` value this handler handles.
func (h *HatchlingHandler) BuildBackend() string { return hatchlingBackend }

func (h *HatchlingHandler) ModuleName(pyproject string, name string) (string, error) {
	return builtinModuleName(hatchlingBackend, pyproject, name)
}

func (h *HatchlingHandler) ModulePaths(pyproject string, name string) ([]string, error) {
	return builtinModulePaths(hatchlingBackend, pyproject, name)
}

func (h *HatchlingHandler) SourcePaths(pyproject string, name string) ([]string, error) {
	return builtinSourcePaths(hatchlingBackend, pyproject, name)
}

func (h *HatchlingHandler) BuildInputs(pyproject string, name string) ([]string, error) {
	return builtinBuildInputs(hatchlingBackend, pyproject, name)
}

func (h *HatchlingHandler) Setup(
	// An OCI-compatible container, also known as a Docker container.
	container *dagger.Container,
) *dagger.Container {
	return builtinSetup(hatchlingBackend, container)
}

func (h *HatchlingHandler) Teardown(
	// An OCI-compatible container, also known as a Docker container.
	container *dagger.Container,
) *dagger.Container {
	return builtinTeardown(hatchlingBackend, container)
}

// Handles packages built with pdm-backend.
type PdmBackendHandler struct{}

const pdmBackend = "pdm.backend"

// The `[build-system].build-backend` value this handler handles.
func (h *PdmBackendHandler) BuildBackend() string { return pdmBackend }

func (h *PdmBackendHandler) ModuleName(pyproject string, name string) (string, error) {
	return builtinModuleName(pdmBackend, pyproject, name)
}

func (h *PdmBackendHandler) ModulePaths(pyproject string, name string) ([]string, error) {
	return builtinModulePaths(pdmBackend, pyproject, name)
}

func (h *PdmBackendHandler) SourcePaths(pyproject string, name string) ([]string, error) {
	return builtinSourcePaths(pdmBackend, pyproject, name)
}

func (h *PdmBackendHandler) BuildInputs(pyproject string, name string) ([]string, error) {
	return builtinBuildInputs(pdmBackend, pyproject, name)
}

func (h *PdmBackendHandler) Setup(
	// An OCI-compatible container, also known as a Docker container.
	container *dagger.Container,
) *dagger.Container {
	return builtinSetup(pdmBackend, container)
}

func (h *PdmBackendHandler) Teardown(
	// An OCI-compatible container, also known as a Docker container.
	container *dagger.Container,
) *dagger.Container {
	return builtinTeardown(pdmBackend, container)
}

// Handles packages built with flit_core.
type FlitCoreHandler struct{}

const flitCoreBackend = "flit_core.buildapi"

// The `[build-system].build-backend` value this handler handles.
func (h *FlitCoreHandler) BuildBackend() string { return flitCoreBackend }

func (h *FlitCoreHandler) ModuleName(pyproject string, name string) (string, error) {
	return builtinModuleName(flitCoreBackend, pyproject, name)
}

func (h *FlitCoreHandler) ModulePaths(pyproject string, name string) ([]string, error) {
	return builtinModulePaths(flitCoreBackend, pyproject, name)
}

func (h *FlitCoreHandler) SourcePaths(pyproject string, name string) ([]string, error) {
	return builtinSourcePaths(flitCoreBackend, pyproject, name)
}

func (h *FlitCoreHandler) BuildInputs(pyproject string, name string) ([]string, error) {
	return builtinBuildInputs(flitCoreBackend, pyproject, name)
}

func (h *FlitCoreHandler) Setup(
	// An OCI-compatible container, also known as a Docker container.
	container *dagger.Container,
) *dagger.Container {
	return builtinSetup(flitCoreBackend, container)
}

func (h *FlitCoreHandler) Teardown(
	// An OCI-compatible container, also known as a Docker container.
	container *dagger.Container,
) *dagger.Container {
	return builtinTeardown(flitCoreBackend, container)
}

// Handles packages built with poetry-core.
type PoetryCoreHandler struct{}

const poetryCoreBackend = "poetry.core.masonry.api"

// The `[build-system].build-backend` value this handler handles.
func (h *PoetryCoreHandler) BuildBackend() string { return poetryCoreBackend }

func (h *PoetryCoreHandler) ModuleName(pyproject string, name string) (string, error) {
	return builtinModuleName(poetryCoreBackend, pyproject, name)
}

func (h *PoetryCoreHandler) ModulePaths(pyproject string, name string) ([]string, error) {
	return builtinModulePaths(poetryCoreBackend, pyproject, name)
}

func (h *PoetryCoreHandler) SourcePaths(pyproject string, name string) ([]string, error) {
	return builtinSourcePaths(poetryCoreBackend, pyproject, name)
}

func (h *PoetryCoreHandler) BuildInputs(pyproject string, name string) ([]string, error) {
	return builtinBuildInputs(poetryCoreBackend, pyproject, name)
}

func (h *PoetryCoreHandler) Setup(
	// An OCI-compatible container, also known as a Docker container.
	container *dagger.Container,
) *dagger.Container {
	return builtinSetup(poetryCoreBackend, container)
}

func (h *PoetryCoreHandler) Teardown(
	// An OCI-compatible container, also known as a Docker container.
	container *dagger.Container,
) *dagger.Container {
	return builtinTeardown(poetryCoreBackend, container)
}

// Handles packages built with maturin.
type MaturinHandler struct {
	// Extra build inputs per normalized distribution name: each entry is the
	// name followed by its inputs.
	//
	// +private
	PackageInputs [][]string
}

const maturinBackend = "maturin"

// The `[build-system].build-backend` value this handler handles.
func (h *MaturinHandler) BuildBackend() string { return maturinBackend }

func (h *MaturinHandler) ModuleName(pyproject string, name string) (string, error) {
	return builtinModuleName(maturinBackend, pyproject, name)
}

func (h *MaturinHandler) ModulePaths(pyproject string, name string) ([]string, error) {
	return builtinModulePaths(maturinBackend, pyproject, name)
}

func (h *MaturinHandler) SourcePaths(pyproject string, name string) ([]string, error) {
	return builtinSourcePaths(maturinBackend, pyproject, name)
}

func (h *MaturinHandler) BuildInputs(pyproject string, name string) ([]string, error) {
	inputs, err := builtinBuildInputs(maturinBackend, pyproject, name)
	if err != nil {
		return nil, err
	}
	return append(inputs, h.extraInputs(name)...), nil
}

func (h *MaturinHandler) Setup(
	// An OCI-compatible container, also known as a Docker container.
	container *dagger.Container,
) *dagger.Container {
	return builtinSetup(maturinBackend, container)
}

func (h *MaturinHandler) Teardown(
	// An OCI-compatible container, also known as a Docker container.
	container *dagger.Container,
) *dagger.Container {
	return builtinTeardown(maturinBackend, container)
}

// Configure how the package `name` builds, e.g. with the crates its Cargo workspace keeps above it.
func (h *MaturinHandler) WithPackageConfig(
	name string,
	// Files (or globs) the package's build reads on top of the defaults: package-relative, or workspace-root-relative with `/`.
	extraBuildInputs []string,
) *MaturinHandler {
	name = normalizePackageName(name)
	configs := [][]string{}
	for _, config := range h.PackageInputs {
		if config[0] != name {
			configs = append(configs, config)
		}
	}
	config := append([]string{name}, extraBuildInputs...)
	return &MaturinHandler{PackageInputs: append(configs, config)}
}

func (h *MaturinHandler) extraInputs(name string) []string {
	name = normalizePackageName(name)
	for _, config := range h.PackageInputs {
		if config[0] == name {
			return config[1:]
		}
	}
	return nil
}

// Handlers for the module's built-in backends, to pass as `build_backend_handlers` to `build`, `install` or `venv`.
//
// Use them to configure a backend for some packages, e.g. maturin with the
// crates a package's Cargo workspace keeps above it.
type BuildBackendHandlers struct{}

func (h *BuildBackendHandlers) UvBuild() *UvBuildHandler { return &UvBuildHandler{} }

func (h *BuildBackendHandlers) Hatchling() *HatchlingHandler { return &HatchlingHandler{} }

func (h *BuildBackendHandlers) PdmBackend() *PdmBackendHandler { return &PdmBackendHandler{} }

func (h *BuildBackendHandlers) FlitCore() *FlitCoreHandler { return &FlitCoreHandler{} }

func (h *BuildBackendHandlers) PoetryCore() *PoetryCoreHandler { return &PoetryCoreHandler{} }

func (h *BuildBackendHandlers) Maturin() *MaturinHandler { return &MaturinHandler{} }
