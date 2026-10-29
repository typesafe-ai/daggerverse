package uv

import (
	"context"
	"fmt"
	"path/filepath"
	"slices"
	"strings"

	"dagger.io/dagger/core"
	"go.opentelemetry.io/otel/attribute"

	"dagger/uv/internal/dagger"
)

var busybox = pinnedImage("busybox")

// Prints every symlink under the current directory whose target is missing.
const danglingLinks = `find . -type l ! -exec test -e {} \; -print`

// 1980-01-01, the earliest timestamp a wheel (zip) can hold. Dagger git trees
// normalize mtimes to 1, which backends that stamp files from their mtime (flit)
// reject unless SOURCE_DATE_EPOCH is set.
const sourceDateEpoch = "315532800"

// An in-progress workspace build: a container plus its resolved sync plan.
//
// Drives the install pipeline: `with_remote_dependencies` to install remote
// deps, `with_workspace_files` to scaffold local packages, then
// `with_local_dependencies` to install them.
type UvWorkspaceBuild struct {
	Container *dagger.Container
	Plan      *UvSyncPlan
}

// execStep runs `argv` in the build container under a span, returning a new build with the result.
//
// `WithExec` is lazy; Sync inside the span so it captures the actual work
// rather than just the query-graph construction.
func (b *UvWorkspaceBuild) execStep(ctx context.Context, spanName string, argv []string, attributes ...attribute.KeyValue) (*UvWorkspaceBuild, error) {
	ctx, span := Tracer().Start(ctx, spanName)
	defer span.End()
	span.SetAttributes(attributes...)
	ctr, err := b.Container.WithExec(argv).Sync(ctx)
	if err != nil {
		return nil, err
	}
	return b.WithContainer(ctr), nil
}

// Copy the uv binary into the build container.
//
// Useful when using a custom `base_container` that doesn't ship uv.
// Copies the static binary from the official distroless image to `/uv/uv`
// and prepends `/uv` to `$PATH`.
func (b *UvWorkspaceBuild) WithUv(
	ctx context.Context,
	// uv version to install. Defaults to the version detected from the workspace.
	// +optional
	version string,
) (*UvWorkspaceBuild, error) {
	v := version
	if v == "" {
		v = defaultVersion
	}
	ctx, span := Tracer().Start(ctx, "install uv binary")
	defer span.End()
	span.SetAttributes(attribute.String("uv.version", v), attribute.String("uv.image", imageRef(v)))
	uvBin := core.NewContainer().From(imageRef(v)).File("/uv")
	ctr, err := b.Container.WithFile("/uv/uv", uvBin).
		WithEnvVariable("PATH", "/uv:${PATH}", core.ContainerWithEnvVariableOpts{Expand: true}).
		Sync(ctx)
	if err != nil {
		return nil, err
	}
	return b.WithContainer(ctr), nil
}

// Install remote (non-local) dependencies via `uv sync --no-install-local`.
//
// When `prune_cache` is set (the default), the
// install is followed by a size-gated `uv cache prune --ci` (see
// `with_cache_prune`): the cache is pruned only once it exceeds
// `max_cache_size` GiB, and `lock_timeout` bounds how long that prune waits
// for the cache lock.
func (b *UvWorkspaceBuild) WithRemoteDependencies(
	ctx context.Context,
	// Whether to run `uv cache prune --ci` after installing remote dependencies. Learn more about the reasoning in [uv docs](https://docs.astral.sh/uv/concepts/cache/#caching-in-continuous-integration).
	// +default=true
	pruneCache bool,
	// Prune the uv cache only once it grows past this many GiB.
	// +default=100
	maxCacheSize int,
	// Seconds uv waits to acquire the cache file lock (`UV_LOCK_TIMEOUT`) while pruning the cache.
	// +default=600
	lockTimeout int,
) (*UvWorkspaceBuild, error) {
	args := slices.Concat(b.Plan.UvSyncArgs, []string{"--no-install-local"})
	build, err := b.execStep(ctx, "install remote dependencies", args, attribute.StringSlice("uv.sync_args", args))
	if err != nil {
		return nil, err
	}
	if pruneCache {
		return build.WithCachePrune(ctx, maxCacheSize, lockTimeout)
	}
	return build, nil
}

// Prune the uv cache with `uv cache prune --ci`, gated on cache size.
//
// Measures the cache dir (`uv cache dir`) and only prunes when it exceeds `max_cache_size`
// GiB, so most builds skip the prune (and never take the cache lock). Because
// `uv cache prune --ci` keeps only compiled wheels (nearly a full clean), one
// prune resets the footprint. `lock_timeout` sets `UV_LOCK_TIMEOUT` for the
// prune so concurrent builds sharing the cache volume wait instead of erroring.
// Set `max_cache_size=0` to prune on every build.
//
// The size check and conditional prune run in a single exec so the live cache
// volume is measured each time this step actually runs, rather than reading a
// stale, separately-cached size.
//
// Learn more about the reasoning in [uv docs](https://docs.astral.sh/uv/concepts/cache/#caching-in-continuous-integration).
func (b *UvWorkspaceBuild) WithCachePrune(
	ctx context.Context,
	// Prune the uv cache only once it grows past this many GiB.
	// +default=100
	maxCacheSize int,
	// Seconds uv waits to acquire the cache file lock (`UV_LOCK_TIMEOUT`) while pruning the cache.
	// +default=600
	lockTimeout int,
) (*UvWorkspaceBuild, error) {
	limitKib := maxCacheSize * 1024 * 1024 // GiB -> KiB, to compare with `du -sk`
	gate := "true"
	if maxCacheSize > 0 {
		gate = fmt.Sprintf(`[ "${used_kib:-0}" -gt %d ]`, limitKib)
	}
	// `-x` keeps du on the cache mount's own filesystem (don't wander into
	// other mounts nested under it).
	script := `used_kib=$(du -skx "$(uv cache dir)" 2>/dev/null | cut -f1 || echo 0); ` +
		fmt.Sprintf("if %s; then ", gate) +
		fmt.Sprintf(`echo "uv cache: ${used_kib} KiB used > %d KiB limit; pruning" >&2; `, limitKib) +
		fmt.Sprintf("UV_LOCK_TIMEOUT=%d uv cache prune --ci; ", lockTimeout) +
		"else " +
		fmt.Sprintf(`echo "uv cache: ${used_kib} KiB used <= %d KiB limit; skipping prune" >&2; `, limitKib) +
		"fi"
	ctx, span := Tracer().Start(ctx, "prune uv cache")
	defer span.End()
	span.SetAttributes(attribute.Int("uv.cache_max_size_gib", maxCacheSize), attribute.Int("uv.cache_lock_timeout", lockTimeout))
	executed := b.Container.WithExec([]string{"sh", "-c", script})
	ctr, err := executed.Sync(ctx)
	if err != nil {
		return nil, err
	}
	// uv (and the gate) report on stderr what was measured / removed; surface
	// it so the span shows the outcome rather than just the command that ran.
	stderr, err := executed.Stderr(ctx)
	if err != nil {
		return nil, err
	}
	if summary := strings.TrimSpace(stderr); summary != "" {
		span.SetAttributes(attribute.String("uv.cache_prune_summary", summary))
	}
	return b.WithContainer(ctr), nil
}

// Install a managed Python via `uv python install`.
//
// Useful on a bare base with no system Python; pass the version the
// workspace's `requires-python` resolves to.
func (b *UvWorkspaceBuild) WithPythonInstall(
	ctx context.Context,
	// Python version to install via `uv python install` (e.g. `3.12`, `3.13.7`).
	version string,
) (*UvWorkspaceBuild, error) {
	argv := []string{"uv", "python", "install", version}
	return b.execStep(ctx, "install python "+version, argv, attribute.String("uv.python_version", version))
}

// Pin the project's Python with `uv python pin` (writes `.python-version`).
//
// Makes subsequent `uv sync` select this exact version.
func (b *UvWorkspaceBuild) WithPythonPin(
	ctx context.Context,
	// Python version to pin via `uv python pin` (writes a `.python-version` file).
	version string,
) (*UvWorkspaceBuild, error) {
	argv := []string{"uv", "python", "pin", version}
	return b.execStep(ctx, "pin python "+version, argv, attribute.String("uv.python_version", version))
}

// Export this build's virtual environment together with the Python it needs.
//
// Bundles the venv and the exact interpreter it links against into a
// `UvVenv` (see `UvVenv.create`). Call after the venv is populated (e.g.
// after `with_remote_dependencies`/`with_local_dependencies`). Requires a
// relocatable venv built against a uv-managed Python; raises otherwise.
func (b *UvWorkspaceBuild) Venv(ctx context.Context) (*UvVenv, error) {
	workdir, err := b.Container.Workdir(ctx)
	if err != nil {
		return nil, err
	}
	return newUvVenv(ctx, b.Container, pjoin(workdir, ".venv"))
}

// Copy this build's venv (and the uv-managed Python it needs) into `container`.
//
// Convenience over `venv().into(...)`: mounts the relocatable venv at `path`
// and its Python at the absolute path the venv expects, yielding a container
// that can run the environment without uv. Same constraints as `venv`.
func (b *UvWorkspaceBuild) CopyVenv(
	ctx context.Context,
	// Container to copy the venv and its Python into.
	container *dagger.Container,
	// Where to mount the venv; relative paths resolve against the container's workdir. Defaults to `.venv`.
	// +default=".venv"
	path string,
	// Also set the standard activation env vars (`VIRTUAL_ENV` and a `PATH` with the venv's `bin/` first).
	// +default=false
	setEnvVars bool,
) (*dagger.Container, error) {
	venv, err := b.Venv(ctx)
	if err != nil {
		return nil, err
	}
	return venv.Into(ctx, container, path, setEnvVars)
}

// scaffoldPackage scaffolds a single package stub (pyproject.toml + README + empty module).
func (b *UvWorkspaceBuild) scaffoldPackage(overlay *dagger.Directory, workdir string, pkg *LocalPackage) *dagger.Directory {
	ctrBase := normpath(pjoin(workdir, pkg.Path))
	overlay = overlay.WithNewFile(strings.TrimLeft(pjoin(ctrBase, "pyproject.toml"), "/"), pkg.PyprojectContents)
	if b.Plan.isFlat(pkg.Name) {
		return overlay
	}
	overlay = overlay.WithNewFile(strings.TrimLeft(pjoin(ctrBase, "README.md"), "/"), "")
	for _, modulePath := range pkg.modulePaths() {
		// A single-file module is stubbed as itself, a package by its `__init__.py`.
		stub := modulePath
		if !isFile(modulePath) {
			stub = pjoin(modulePath, "__init__.py")
		}
		overlay = overlay.WithNewFile(strings.TrimLeft(pjoin(ctrBase, stub), "/"), "")
	}
	// Editable installs build metadata from the scaffold before sources are copied.
	var packageInputs, rootInputs []string
	for _, input := range pkg.buildInputs() {
		if strings.HasPrefix(input, "/") {
			rootInputs = append(rootInputs, strings.TrimLeft(input, "/"))
		} else {
			packageInputs = append(packageInputs, input)
		}
	}
	if len(packageInputs) > 0 {
		resolved := normpath(pjoin(b.Plan.WorkspacePath, pkg.Path))
		overlay = overlay.WithDirectory(
			strings.TrimLeft(ctrBase, "/"),
			pkg.Source.Directory(resolved),
			core.DirectoryWithDirectoryOpts{Include: packageInputs},
		)
	}
	if len(rootInputs) > 0 {
		// Workspace-root-relative inputs, e.g. a Cargo workspace above the package.
		root := strings.TrimLeft(b.rootPath(workdir), "/")
		if root == "" {
			root = "."
		}
		overlay = overlay.WithDirectory(root, b.Plan.SourceDir, core.DirectoryWithDirectoryOpts{Include: rootInputs})
	}
	return overlay
}

// rootPath is where the workspace root sits in the container, which mirrors it with the uv workspace at `workdir`.
func (b *UvWorkspaceBuild) rootPath(workdir string) string {
	up, err := filepath.Rel("/"+b.Plan.WorkspacePath, "/")
	if err != nil {
		panic(err)
	}
	return normpath(pjoin(workdir, up))
}

// namedHooks are a backend's `setup`/`teardown`, keyed for the trace.
type namedHooks struct {
	name  string
	hooks backendHooks
}

// hooks are the `setup`/`teardown` around the local packages' build, once each, keyed for the trace.
//
// A package's handler provides them; without one, its built-in backend, if it needs any.
func (b *UvWorkspaceBuild) hooks() []namedHooks {
	var hooks []namedHooks
	add := func(name string, h backendHooks) {
		if !slices.ContainsFunc(hooks, func(n namedHooks) bool { return n.name == name }) {
			hooks = append(hooks, namedHooks{name, h})
		}
	}
	for _, pkg := range b.Plan.NeededLocal {
		backend := pkg.buildBackend().name
		if b.Plan.isFlat(pkg.Name) || backend == nil {
			continue
		}
		if pkg.Handler != nil {
			add(fmt.Sprintf("%s handler #%d", *backend, *pkg.Handler), handlerHooks{b.Plan.Handlers[*pkg.Handler]})
		} else if builtin, ok := backends[*backend]; ok && builtin.hooks != nil {
			add(*backend, builtin.hooks)
		}
	}
	return hooks
}

// scaffold scaffolds package stubs (pyproject.toml + README + empty module) for `packages`.
//
// The per-package `WithNewFile` calls are lazy, so the span
// forces evaluation with Sync before closing; otherwise it would capture
// only Go graph-building and report ~zero duration.
func (b *UvWorkspaceBuild) scaffold(ctx context.Context, packages []*LocalPackage, workdir, spanName string) (*dagger.Container, error) {
	overlay := core.NewDirectory()
	ctx, span := Tracer().Start(ctx, spanName)
	defer span.End()
	span.SetAttributes(attribute.Int("packages.count", len(packages)), attribute.StringSlice("packages.names", packageNames(packages)))
	for _, pkg := range packages {
		overlay = b.scaffoldPackage(overlay, workdir, pkg)
	}
	return b.Container.WithDirectory("/", overlay).Sync(ctx)
}

// Scaffold needed local package stubs (pyproject.toml + empty src/) into the container.
func (b *UvWorkspaceBuild) WithWorkspaceFiles(ctx context.Context) (*UvWorkspaceBuild, error) {
	workdir, err := b.Container.Workdir(ctx)
	if err != nil {
		return nil, err
	}
	ctr, err := b.scaffold(ctx, b.Plan.NeededLocal, workdir, "scaffold local dependencies")
	if err != nil {
		return nil, err
	}
	return &UvWorkspaceBuild{Container: ctr, Plan: b.Plan}, nil
}

// Like with_workspace_files but scaffolds every local package, not just transitive deps.
func (b *UvWorkspaceBuild) WithAllWorkspaceMembers(ctx context.Context) (*UvWorkspaceBuild, error) {
	workdir, err := b.Container.Workdir(ctx)
	if err != nil {
		return nil, err
	}
	ctr, err := b.scaffold(ctx, b.Plan.AllLocal, workdir, "scaffold all workspace members")
	if err != nil {
		return nil, err
	}
	return &UvWorkspaceBuild{Container: ctr, Plan: b.Plan}, nil
}

// Return a new UvWorkspaceBuild with a different container but the same plan.
func (b *UvWorkspaceBuild) WithContainer(
	// Replacement container (e.g. after installing non-Python packages)
	container *dagger.Container,
) *UvWorkspaceBuild {
	return &UvWorkspaceBuild{Container: container, Plan: b.Plan}
}

func (b *UvWorkspaceBuild) copyPackage(overlay *dagger.Directory, workdir string, pkg *LocalPackage) *dagger.Directory {
	resolved := normpath(pjoin(b.Plan.WorkspacePath, pkg.Path))
	ctrBase := normpath(pjoin(workdir, pkg.Path))
	for _, sourcePath := range pkg.sourcePaths() {
		target := strings.TrimLeft(pjoin(ctrBase, sourcePath), "/")
		source := pjoin(resolved, sourcePath)
		if isFile(sourcePath) {
			overlay = overlay.WithFile(target, pkg.Source.File(source))
		} else {
			overlay = overlay.WithDirectory(target, pkg.Source.Directory(source))
		}
	}
	return overlay
}

// copySources copies real source for each needed local package into `ctr`.
//
// The per-package `WithDirectory` calls are lazy, so the span forces
// evaluation with Sync before closing; otherwise it would capture only
// Go graph-building and report ~zero duration.
func (b *UvWorkspaceBuild) copySources(ctx context.Context, ctr *dagger.Container, workdir string) (*dagger.Container, error) {
	overlay := core.NewDirectory()
	ctx, span := Tracer().Start(ctx, "copy local dependency sources")
	defer span.End()
	span.SetAttributes(attribute.Int("packages.count", len(b.Plan.NeededLocal)), attribute.StringSlice("packages.names", packageNames(b.Plan.NeededLocal)))
	for _, pkg := range b.Plan.NeededLocal {
		if b.Plan.isFlat(pkg.Name) {
			continue
		}
		overlay = b.copyPackage(overlay, workdir, pkg)
	}
	overlay, err := b.withLinkTargets(ctx, overlay, workdir)
	if err != nil {
		return nil, err
	}
	return ctr.WithDirectory("/", overlay).Sync(ctx)
}

// withLinkTargets replaces copied symlinks that point outside the copied sources with their content.
//
// Packages can link to code elsewhere in the repository (e.g. modules vendored
// from a sibling directory). A busybox exec lists the dangling links, since the
// Dagger API has no way to list them; Dagger follows each link in the source
// tree, and its content replaces the link. Repeats while that content brings
// new dangling links.
func (b *UvWorkspaceBuild) withLinkTargets(ctx context.Context, overlay *dagger.Directory, workdir string) (*dagger.Directory, error) {
	seen := map[string]bool{}
	source := b.Plan.SourceDir
	ctx, span := Tracer().Start(ctx, "resolve dangling symlinks")
	defer span.End()
	for {
		listing, err := core.NewContainer().
			From(busybox).
			WithDirectory("/overlay", overlay).
			WithWorkdir("/overlay").
			WithExec([]string{"sh", "-c", danglingLinks}).
			Stdout(ctx)
		if err != nil {
			return nil, err
		}
		var links []string
		for _, link := range splitLines(listing) {
			link = strings.TrimPrefix(link, "./")
			if !seen[link] {
				links = append(links, link)
			}
		}
		if len(links) == 0 {
			span.SetAttributes(attribute.StringSlice("links.resolved", sortedKeys(seen)))
			return overlay, nil
		}
		for _, link := range links {
			seen[link] = true
		}
		for _, link := range links {
			// The container mirrors the source tree with the workspace root at `workdir`.
			relative, err := filepath.Rel(workdir, "/"+link)
			if err != nil {
				return nil, err
			}
			sourcePath := normpath(pjoin(b.Plan.WorkspacePath, relative))
			isDir, err := source.Exists(ctx, sourcePath, core.DirectoryExistsOpts{ExpectedType: core.ExistsTypeDirectoryType})
			if err != nil {
				return nil, err
			}
			if isDir {
				overlay = overlay.WithoutFile(link).WithDirectory(link, source.Directory(sourcePath))
				continue
			}
			exists, err := source.Exists(ctx, sourcePath)
			if err != nil {
				return nil, err
			}
			if exists {
				overlay = overlay.WithoutFile(link).WithFile(link, source.File(sourcePath))
			}
		}
	}
}

// syncLocal builds and installs the local packages, with the backends' hooks around the `uv sync`.
func (b *UvWorkspaceBuild) syncLocal(ctx context.Context, ctr *dagger.Container) (*dagger.Container, error) {
	ctx, span := Tracer().Start(ctx, "install local dependencies")
	defer span.End()
	hooks := b.hooks()
	names := make([]string, len(hooks))
	for i, h := range hooks {
		names[i] = h.name
	}
	span.SetAttributes(attribute.StringSlice("uv.sync_args", b.Plan.UvSyncArgs), attribute.StringSlice("build.backend_hooks", names))
	for _, h := range hooks {
		ctr = h.hooks.setup(ctr)
	}
	// Set SOURCE_DATE_EPOCH for this sync only, keeping a value the base sets.
	epoch, err := ctr.EnvVariable(ctx, "SOURCE_DATE_EPOCH")
	if err != nil {
		return nil, err
	}
	setEpoch := epoch == ""
	if setEpoch {
		ctr = ctr.WithEnvVariable("SOURCE_DATE_EPOCH", sourceDateEpoch)
	}
	ctr = ctr.WithExec(b.Plan.UvSyncArgs)
	if setEpoch {
		ctr = ctr.WithoutEnvVariable("SOURCE_DATE_EPOCH")
	}
	for _, h := range slices.Backward(hooks) {
		ctr = h.hooks.teardown(ctr)
	}
	// `WithExec` is lazy; Sync inside the span so it captures the actual
	// install rather than just the query-graph construction.
	return ctr.Sync(ctx)
}

// Install the scaffolded local packages, copying their real source at the right time.
//
// For **editable** installs (the default), run `uv sync` against the package
// stubs from `with_workspace_files`, then copy real source over the stubs last.
// Editable installs are path links, so the source goes live without a re-sync
// and source-only changes don't invalidate the cached install layer.
//
// For **non-editable** installs (`no_editable=True`), `uv sync` builds a wheel
// from whatever source is present and bakes it into `site-packages`, so the real
// source must be copied in *before* the sync: there are no path links for a
// copy-last to make live, and syncing against the stubs would bake empty modules.
func (b *UvWorkspaceBuild) WithLocalDependencies(ctx context.Context) (*dagger.Container, error) {
	workdir, err := b.Container.Workdir(ctx)
	if err != nil {
		return nil, err
	}
	if b.Plan.NoEditable {
		ctr, err := b.copySources(ctx, b.Container, workdir)
		if err != nil {
			return nil, err
		}
		return b.syncLocal(ctx, ctr)
	}
	ctr, err := b.syncLocal(ctx, b.Container)
	if err != nil {
		return nil, err
	}
	// Copy real source last: the editable installs above already point at these
	// paths, so the code goes live with no re-sync, keeping the install layer
	// cached across source-only changes.
	return b.copySources(ctx, ctr, workdir)
}
