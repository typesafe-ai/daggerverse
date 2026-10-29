package uv

import (
	"context"
	"fmt"
	"slices"
	"strings"

	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/trace"
	"golang.org/x/sync/errgroup"

	"dagger/uv/internal/dagger"
)

// matchReachable keeps only local packages whose resolved path has a pyproject.toml in the source tree.
func matchReachable(packages orderedPackages, workspacePath string, pyprojectPaths []string) orderedPackages {
	existing := map[string]bool{}
	for _, p := range pyprojectPaths {
		existing[normpath(dirname(p))] = true
	}
	result := orderedPackages{}
	for name, p := range packages {
		if existing[normpath(pjoin(workspacePath, p))] {
			result[name] = p
		}
	}
	return result
}

// handlersByBackend is the index of each backend's handler in `backends`, the backends the handlers handle.
//
// Errors when two handlers handle the same backend: one handler configures every
// package that uses it.
func handlersByBackend(backends []string) (map[string]int, error) {
	var duplicates []string
	for i, backend := range backends {
		if slices.Contains(backends[i+1:], backend) && !slices.Contains(duplicates, backend) {
			duplicates = append(duplicates, backend)
		}
	}
	if len(duplicates) > 0 {
		slices.Sort(duplicates)
		return nil, fmt.Errorf("more than one build backend handler for %s; pass one per backend.", strings.Join(duplicates, ", "))
	}
	byBackend := map[string]int{}
	for i, backend := range backends {
		byBackend[backend] = i
	}
	return byBackend, nil
}

// fallBackLikeBuiltin restores a handler's "fall back to the generic layout" for a built-in backend.
//
// The Go SDK can't declare a nullable list in an interface or a field, so a
// handler can't return None for `module_paths` or `source_paths`, and the engine
// turns a nil list into an empty one. An empty list where the module's own
// support for the backend falls back to the generic layout falls back here too,
// so a built-in handler (which answers like that support) builds like it.
func fallBackLikeBuiltin(layout *BuildBackendLayout, metadata map[string]any, name string) *BuildBackendLayout {
	backend := optionalString(table(metadata, "build-system"), "build-backend")
	if backend == nil {
		return layout
	}
	builtin, ok := backends[*backend]
	if !ok {
		return layout
	}
	settings := builtin.parse(metadata, name)
	restored := *layout
	if len(layout.ModulePaths) == 0 && settings.modulePaths() == nil {
		restored.ModulePaths = nil
	}
	if len(layout.SourcePaths) == 0 && settings.sourcePaths() == nil {
		restored.SourcePaths = nil
	}
	return &restored
}

var moduleNameOverrides = map[string]string{
	"dagger-io": "dagger",
}

func moduleNameOf(pkgName string) string {
	if name, ok := moduleNameOverrides[pkgName]; ok {
		return name
	}
	return strings.ReplaceAll(pkgName, "-", "_")
}

func transitiveUnion(lockData map[string]any, packages []string, groups map[string]bool) orderedPackages {
	merged := orderedPackages{}
	for _, pkg := range packages {
		for name, p := range findTransitiveLocalDeps(lockData, pkg, groups) {
			merged[name] = p
		}
	}
	return merged
}

// discoverLocalPackages finds the reachable local packages: `(allLocal, neededLocal)`.
//
// Reads only the manifests (lockfiles and `pyproject.toml` files), so it never
// loads package sources.
//
// `neededLocal` mirrors `uv sync`'s selection: every member when
// `allPackages` is set, the union of the explicit `packages`' transitive
// local deps when given, otherwise the transitive local deps of
// `defaultPackage` (the current package, à la a bare `uv sync`). Only the
// targets' own selected dependency `groups` are followed.
func discoverLocalPackages(
	ctx context.Context,
	lockData map[string]any,
	packages []string,
	allPackages bool,
	defaultPackage string,
	groups map[string]bool,
	workspacePath string,
	manifestsDir *dagger.Directory,
) (orderedPackages, orderedPackages, error) {
	ctx, span := Tracer().Start(ctx, "discover local packages")
	defer span.End()
	// Glob the pyproject.toml paths once and reuse them for both all-local and
	// needed-local reachability filtering.
	pyprojectPaths, err := manifestsDir.Glob(ctx, "**/pyproject.toml")
	if err != nil {
		return nil, nil, err
	}
	allLocal := matchReachable(parseLocalPackages(lockData), workspacePath, pyprojectPaths)
	neededLocal := allLocal
	if !allPackages {
		// newUvSyncPlan's requirePackageSelection guarantees a target here:
		// the explicit packages, or the current (workspace-root) package.
		targets := packages
		if len(targets) == 0 && defaultPackage != "" {
			targets = []string{defaultPackage}
		}
		neededLocal = matchReachable(transitiveUnion(lockData, targets, groups), workspacePath, pyprojectPaths)
	}
	span.SetAttributes(
		attribute.Int("packages.all", len(allLocal)),
		attribute.Int("packages.needed", len(neededLocal)),
		attribute.StringSlice("packages.names", sortedKeys(allLocal)),
	)
	return allLocal, neededLocal, nil
}

// A project's files: the project and its local dependencies, at workspace-root-relative paths.
type ProjectSource struct {
	// Project root, relative to the workspace root
	Path string
	// The project's files and those of its local dependencies
	Source *dagger.Directory
}

// LocalPackage sits in a type group because gofmt rewrites doubled backquotes
// in a top-level doc comment, and its description must keep them.
type (
	// A local (editable/directory/virtual) package in a uv workspace.
	//
	// ``pyproject_contents`` is captured while resolving the plan so scaffold
	// layers depend on package metadata, not on the whole source directory.
	LocalPackage struct {
		// Package name
		Name string
		// Workspace-relative path
		Path string
		// Python import name; dotted for a module inside namespace packages
		Module string
		// Flat layout (module at package root) vs src layout (module under src/)
		Flat bool
		// Already-read package metadata used when creating a dependency scaffold
		PyprojectContents string
		// The package's files and those of its local dependencies, at workspace-root-relative paths
		Source *dagger.Directory
		// Layout resolved from a build backend handler, replacing the built-in backend settings
		Layout *BuildBackendLayout
		// Index of the plan's `handlers` entry for this package's backend; null for built-in support
		Handler *int
	}
)

// buildBackend is the build backend and its settings, derived from the captured package metadata.
func (p *LocalPackage) buildBackend() packageBackend {
	metadata, _ := loadsToml(p.PyprojectContents)
	var settings backendSettings
	if p.Layout != nil {
		settings = fallBackLikeBuiltin(p.Layout, metadata, p.Name)
	}
	return packageBackendFromPyproject(metadata, p.Name, settings)
}

// modulePaths are package-relative import directories (or `.py` modules) used to scaffold modules.
func (p *LocalPackage) modulePaths() []string {
	if paths := p.buildBackend().modulePaths(); paths != nil {
		return paths
	}
	modulePath := strings.ReplaceAll(p.Module, ".", "/")
	if p.Flat {
		return []string{modulePath}
	}
	return []string{pjoin("src", modulePath)}
}

// sourcePaths are the copy source roots, or only declared modules for a flat layout.
func (p *LocalPackage) sourcePaths() []string {
	if paths := p.buildBackend().sourcePaths(); paths != nil {
		return paths
	}
	if p.Flat {
		return []string{strings.ReplaceAll(p.Module, ".", "/")}
	}
	return []string{"src"}
}

// buildInputs are package-relative files (or globs) staged into the scaffold for the backend.
func (p *LocalPackage) buildInputs() []string {
	return p.buildBackend().buildInputs()
}

// Resolved build configuration for a uv workspace sync.
type UvSyncPlan struct {
	// The workspace root's manifests: uv.lock, uv.toml and pyproject.toml files
	WsDir *dagger.Directory
	// Every member's files; read only for files outside the needed packages, e.g. symlink targets
	SourceDir *dagger.Directory
	// Workspace path within the source directory
	WorkspacePath string
	// Every local package in the workspace (sorted by name)
	AllLocal []*LocalPackage
	// Local packages the target transitively depends on (sorted by name)
	NeededLocal []*LocalPackage
	// Virtual/deps-only packages: pyproject scaffolded, source skipped
	FlatPackages []string
	// Precomputed uv sync argv
	UvSyncArgs []string
	// Local packages installed non-editable (source baked into site-packages, not path-linked)
	NoEditable bool
	// Build backend handlers, whose hooks run around the local packages' build
	Handlers []BuildBackendHandler
}

type syncPlanOptions struct {
	sourceDir       *dagger.Directory
	manifestsDir    *dagger.Directory
	projects        []*ProjectSource
	srcInitPaths    []string
	workspacePath   string
	packages        []string
	extras          []string
	groups          []string
	allExtras       bool
	allGroups       bool
	allPackages     bool
	noEditable      bool
	noDefaultGroups bool
	handlers        []BuildBackendHandler
}

// newUvSyncPlan parses uv.lock and resolves all build configuration up front.
//
// Reads only `manifestsDir`; each package's sources come from its project's
// directory in `projects` and are loaded only when the build copies them.
func newUvSyncPlan(ctx context.Context, o syncPlanOptions) (*UvSyncPlan, error) {
	wsDir := o.manifestsDir
	if o.workspacePath != "." {
		wsDir = o.manifestsDir.Directory(o.workspacePath)
	}
	packages := make([]string, len(o.packages))
	for i, p := range o.packages {
		packages[i] = normalizePackageName(p)
	}
	lock, err := wsDir.File("uv.lock").Contents(ctx)
	if err != nil {
		return nil, err
	}
	lockData, err := loadsToml(lock)
	if err != nil {
		return nil, err
	}

	// The workspace root's package name is `uv sync`'s implicit target when no
	// package is requested; resolve it so we mirror that default.
	rootPyproject, err := wsDir.File("pyproject.toml").Contents(ctx)
	if err != nil {
		return nil, err
	}
	rootName, err := parseProjectName(rootPyproject)
	if err != nil {
		return nil, err
	}
	defaultPackage := ""
	if rootName != nil && *rootName != "" {
		defaultPackage = normalizePackageName(*rootName)
	}
	// Fail fast on a pure workspace root with no selection (nothing to install).
	if err := requirePackageSelection(packages, o.allPackages, defaultPackage); err != nil {
		return nil, err
	}

	groups, err := selectedGroups(rootPyproject, o.groups, o.allGroups, o.noDefaultGroups)
	if err != nil {
		return nil, err
	}
	allLocal, neededLocal, err := discoverLocalPackages(
		ctx, lockData, packages, o.allPackages, defaultPackage, groups, o.workspacePath, o.manifestsDir,
	)
	if err != nil {
		return nil, err
	}
	projectSources := map[string]*dagger.Directory{}
	for _, project := range o.projects {
		projectSources[project.Path] = project.Source
	}
	srcInits := map[string]bool{}
	for _, p := range o.srcInitPaths {
		srcInits[normpath(p)] = true
	}

	// A local package with no [build-system] or with package = false is deps-only:
	// uv installs its dependencies but never builds the package itself, so its
	// source must not be scaffolded or copied, only its pyproject.toml (for
	// dependency resolution). This holds whether the package is a build target
	// or a transitive workspace dependency (e.g. a Pulumi program whose code
	// lives at the package root, with no src/ or module dir to copy).
	handlers := o.handlers
	if handlers == nil {
		handlers = []BuildBackendHandler{}
	}
	handled := make([]string, len(handlers))
	eg, gctx := errgroup.WithContext(ctx)
	for i, handler := range handlers {
		eg.Go(func() (err error) {
			handled[i], err = handler.BuildBackend(gctx)
			return err
		})
	}
	if err := eg.Wait(); err != nil {
		return nil, err
	}
	byBackend, err := handlersByBackend(handled)
	if err != nil {
		return nil, err
	}
	flatPackages := []string{}
	localPackages := map[string]*LocalPackage{}
	err = func() error {
		ctx, span := Tracer().Start(ctx, "resolve local package layouts")
		defer span.End()
		span.SetAttributes(attribute.Int("packages.count", len(allLocal)))
		for _, name := range sortedKeys(allLocal) {
			pkgPath := allLocal[name]
			resolved := normpath(pjoin(o.workspacePath, pkgPath))
			contents, err := o.manifestsDir.File(pjoin(resolved, "pyproject.toml")).Contents(ctx)
			if err != nil {
				return err
			}
			metadata, err := loadsToml(contents)
			if err != nil {
				return err
			}
			_, hasBuildSystem := metadata["build-system"]
			virtual := !hasBuildSystem || table(metadata, "tool", "uv")["package"] == false
			if virtual {
				flatPackages = append(flatPackages, name)
			}
			backendName := optionalString(table(metadata, "build-system"), "build-backend")
			var layout *BuildBackendLayout
			var settings backendSettings
			var handler *int
			if backendName != nil {
				if i, ok := byBackend[*backendName]; ok {
					handler = &i
					if layout, err = resolveBuildBackendLayout(ctx, handlers[i], contents, name); err != nil {
						return err
					}
					settings = layout
				}
			}
			module := moduleNameOf(name)
			if declared := packageBackendFromPyproject(metadata, name, settings).moduleName(); declared != nil && *declared != "" {
				module = *declared
			}
			// A src layout has the module's top-level package under `src/`.
			top, _, _ := strings.Cut(module, ".")
			source, ok := projectSources[resolved]
			if !ok {
				source = o.sourceDir
			}
			pkg := &LocalPackage{
				Name:              name,
				Path:              pkgPath,
				Module:            module,
				Flat:              !srcInits[normpath(pjoin(resolved, "src", top, "__init__.py"))],
				PyprojectContents: contents,
				Source:            source,
				Layout:            layout,
				Handler:           handler,
			}
			localPackages[name] = pkg
			var modulePaths, sourcePaths []string
			if !virtual {
				modulePaths, sourcePaths = pkg.modulePaths(), pkg.sourcePaths()
			}
			backendLabel := "none"
			if backendName != nil {
				backendLabel = *backendName
			}
			span.AddEvent("package layout", trace.WithAttributes(
				attribute.String("package.name", name),
				attribute.String("package.path", pkgPath),
				attribute.String("package.backend", backendLabel),
				attribute.Bool("package.backend_supported", pkg.buildBackend().settings != nil),
				attribute.Bool("package.backend_handler", handler != nil),
				attribute.Bool("package.virtual", virtual),
				attribute.StringSlice("package.module_paths", modulePaths),
				attribute.StringSlice("package.source_paths", sourcePaths),
			))
		}
		return nil
	}()
	if err != nil {
		return nil, err
	}

	syncArgs := buildUvSyncArgs(uvSyncOptions{
		packages:        packages,
		extras:          o.extras,
		groups:          o.groups,
		allExtras:       o.allExtras,
		allGroups:       o.allGroups,
		allPackages:     o.allPackages,
		noEditable:      o.noEditable,
		noDefaultGroups: o.noDefaultGroups,
	})

	plan := &UvSyncPlan{
		WsDir:         wsDir,
		SourceDir:     o.sourceDir,
		WorkspacePath: o.workspacePath,
		AllLocal:      []*LocalPackage{},
		NeededLocal:   []*LocalPackage{},
		FlatPackages:  flatPackages,
		UvSyncArgs:    syncArgs,
		NoEditable:    o.noEditable,
		Handlers:      handlers,
	}
	for _, name := range sortedKeys(allLocal) {
		plan.AllLocal = append(plan.AllLocal, localPackages[name])
	}
	for _, name := range sortedKeys(neededLocal) {
		plan.NeededLocal = append(plan.NeededLocal, localPackages[name])
	}
	return plan, nil
}

func (p *UvSyncPlan) isFlat(name string) bool {
	return slices.Contains(p.FlatPackages, name)
}

func packageNames(packages []*LocalPackage) []string {
	names := make([]string, len(packages))
	for i, pkg := range packages {
		names[i] = pkg.Name
	}
	return names
}
