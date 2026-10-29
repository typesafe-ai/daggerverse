package uv

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path"
	"slices"
	"strings"

	"dagger.io/dagger/core"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/trace"
	"golang.org/x/sync/errgroup"

	"dagger/uv/internal/dagger"
)

const venvWarning = "UV_PROJECT_ENVIRONMENT is not set, so uv creates a .venv. Unless you need one, set " +
	"UV_PROJECT_ENVIRONMENT on the base container to its Python's prefix, such as /usr/local."

// A self-contained uv workspace: the source files rooted at a `uv.lock`.
//
// Carries the source tree and the workspace's path within it (`"."` for a
// root workspace), relative to the Dagger workspace root. From those it derives everything it needs to `audit` the
// locked dependencies or `build` a minimal container for a package, so
// the parent `Uv` stays a thin, container-free entrypoint.
//
// Every directory keeps workspace-root-relative paths, so builds can reach a
// sibling like `../my-dep` in a nested workspace. They are lazy: resolving
// and planning read only `manifests`, and a build loads only the `projects`
// it installs. Projects nested in those trees that nothing needs (e.g. test
// fixtures) are left out, so their edits don't invalidate this workspace's
// builds.
//
// Named `UvWorkspaceSource` (not `UvWorkspace`) to avoid colliding with
// the top-level `Uv` constructor when composed in a parent module.
type UvWorkspaceSource struct {
	// The workspace's members and their path dependencies, at workspace-root-relative paths.
	Source *dagger.Directory
	// Only what resolving the workspace reads: uv.lock, uv.toml and the members' pyproject.toml files.
	Manifests *dagger.Directory
	// Each member's and local dependency's files, loaded only when a build installs that project.
	Projects []*ProjectSource
	// The projects' `src/*/__init__.py` files, which mark a `src` layout.
	SrcInitPaths []string
	// Workspace root path (holding uv.lock/pyproject.toml) within `source`. `.` for a root workspace.
	Path string
}

func (w *UvWorkspaceSource) wsDir() *dagger.Directory {
	if w.Path == "." {
		return w.Manifests
	}
	return w.Manifests.Directory(w.Path)
}

// requiredVersion is the `required-version` specifier declared by the workspace, if any.
//
// Prefers `uv.toml` (top-level) over `[tool.uv]` in `pyproject.toml`.
func (w *UvWorkspaceSource) requiredVersion(ctx context.Context) (*string, error) {
	ws := w.wsDir()
	entries, err := ws.Entries(ctx)
	if err != nil {
		return nil, err
	}
	if slices.Contains(entries, "uv.toml") {
		content, err := ws.File("uv.toml").Contents(ctx)
		if err != nil {
			return nil, err
		}
		value, err := parseRequiredVersionFromUvToml(content)
		if err != nil || value != nil {
			return value, err
		}
	}
	content, err := ws.File("pyproject.toml").Contents(ctx)
	if err != nil {
		return nil, err
	}
	return parseRequiredVersionFromPyproject(content)
}

// The uv version this workspace requires, as a concrete image tag.
//
// Reads `required-version` (a PEP 440 specifier) from `uv.toml` or the
// `[tool.uv]` table of `pyproject.toml`. Exact pins are used as-is; ranges
// resolve to their minimal compatible version (no PyPI lookup). Falls back
// to the module's pinned default when unspecified.
func (w *UvWorkspaceSource) UvVersion(ctx context.Context) (string, error) {
	specifier, err := w.requiredVersion(ctx)
	if err != nil {
		return "", err
	}
	if specifier == nil {
		return defaultVersion, nil
	}
	return resolveSpecifier(*specifier)
}

// The Python version pinned by the workspace's `.python-version`, if any.
//
// Returns its contents (e.g. `3.13.7`), or `None` when the file is absent.
// `build` stages this pin so the environment and `uv sync` use the exact interpreter
// instead of resolving `requires-python`.
func (w *UvWorkspaceSource) PythonVersion(ctx context.Context) (*string, error) {
	ws := w.wsDir()
	entries, err := ws.Entries(ctx)
	if err != nil {
		return nil, err
	}
	if !slices.Contains(entries, ".python-version") {
		return nil, nil
	}
	content, err := ws.File(".python-version").Contents(ctx)
	if err != nil {
		return nil, err
	}
	version := strings.TrimSpace(content)
	return &version, nil
}

func memberPathsFrom(pyprojectData map[string]any, allPyprojects []string) []string {
	globs, _ := stringList(table(pyprojectData, "tool", "uv", "workspace")["members"])
	if len(globs) == 0 {
		return []string{}
	}
	var members []string
	for _, p := range allPyprojects {
		d := dirname(p)
		if d == "" {
			d = "."
		}
		if d != "." && slices.ContainsFunc(globs, func(g string) bool { return fullMatch(d, g) }) {
			members = append(members, d)
		}
	}
	slices.Sort(members)
	return members
}

// fullMatch is Python's `PurePosixPath.full_match`: `*` matches within a segment, `**` any number of segments.
func fullMatch(p, pattern string) bool {
	var match func(parts, pats []string) bool
	match = func(parts, pats []string) bool {
		if len(pats) == 0 {
			return len(parts) == 0
		}
		if pats[0] == "**" {
			for i := 0; i <= len(parts); i++ {
				if match(parts[i:], pats[1:]) {
					return true
				}
			}
			return false
		}
		if len(parts) == 0 {
			return false
		}
		ok, err := path.Match(pats[0], parts[0])
		return err == nil && ok && match(parts[1:], pats[1:])
	}
	clean := func(s string) []string {
		return filter(strings.Split(s, "/"), func(part string) bool { return part != "" && part != "." })
	}
	return match(clean(p), clean(pattern))
}

// The package indices configured for this workspace.
//
// Reads `[[index]]` from `uv.toml` when present, otherwise falls back
// to `[[tool.uv.index]]` in `pyproject.toml`, matching uv's own
// precedence (`uv.toml` overrides the entire `[tool.uv]` section).
//
// When `include_from_members` is set, also reads `[[tool.uv.index]]`
// from every workspace member's `pyproject.toml` and merges them
// (member entries take precedence on name collision).
func (w *UvWorkspaceSource) Indices(
	ctx context.Context,
	// Merge indices from all workspace members' pyproject.toml files. When True, member-level indices are included alongside workspace-level indices (member entries win on name collision).
	// +default=false
	includeFromMembers bool,
) ([]*UvIndex, error) {
	ws := w.wsDir()
	content, err := ws.File("pyproject.toml").Contents(ctx)
	if err != nil {
		return nil, err
	}
	pyprojectData, err := loadsToml(content)
	if err != nil {
		return nil, err
	}

	wsRaw, err := func() ([]*UvIndex, error) {
		ctx, span := Tracer().Start(ctx, "read workspace indices")
		defer span.End()
		span.SetAttributes(attribute.String("workspace.path", w.Path))
		raw, err := readWorkspaceIndices(ctx, ws)
		if err != nil {
			return nil, err
		}
		span.SetAttributes(attribute.Int("indices.count", len(raw)), attribute.StringSlice("indices.names", indexNames(raw)))
		return raw, nil
	}()
	if err != nil {
		return nil, err
	}

	if !includeFromMembers {
		return wsRaw, nil
	}

	pyprojects, err := ws.Glob(ctx, "**/pyproject.toml")
	if err != nil {
		return nil, err
	}
	memberPaths := memberPathsFrom(pyprojectData, pyprojects)

	memberResults := make([][]*UvIndex, len(memberPaths))
	eg, gctx := errgroup.WithContext(ctx)
	for i, memberPath := range memberPaths {
		eg.Go(func() error {
			ctx, span := Tracer().Start(gctx, fmt.Sprintf("read member indices (%s)", memberPath))
			defer span.End()
			content, err := ws.Directory(memberPath).File("pyproject.toml").Contents(ctx)
			if err != nil {
				return err
			}
			raw, err := parseIndices(content, false)
			if err != nil {
				return err
			}
			span.SetAttributes(
				attribute.String("member.path", memberPath),
				attribute.Int("indices.count", len(raw)),
				attribute.StringSlice("indices.names", indexNames(raw)),
			)
			memberResults[i] = raw
			return nil
		})
	}
	if err := eg.Wait(); err != nil {
		return nil, err
	}
	memberRaw := slices.Concat(memberResults...)

	_, span := Tracer().Start(ctx, "merge indices")
	defer span.End()
	span.SetAttributes(attribute.Int("indices.workspace_count", len(wsRaw)), attribute.Int("indices.member_count", len(memberRaw)))
	result := mergeIndices(wsRaw, memberRaw)
	span.SetAttributes(attribute.Int("indices.total", len(result)), attribute.StringSlice("indices.names", indexNames(result)))
	return result, nil
}

// A single member package within this workspace.
func (w *UvWorkspaceSource) Package(
	// Member package path relative to the workspace root.
	path string,
) *UvPackageSource {
	return &UvPackageSource{
		Manifests:     w.Manifests,
		WorkspacePath: w.Path,
		PackagePath:   path,
	}
}

// Run `uv audit --frozen` on this workspace's locked dependencies.
//
// Audits straight from the committed uv.lock without re-resolving against
// package indexes (`--frozen`); vulnerability data comes from the public
// OSV service, so no index credentials are required. Only `manifests` is
// mounted, so a source edit leaves the audit cached.
//
// The image is resolved here: explicit `image` > `image_ref(uv_version)` >
// the version detected from the workspace.
//
// +check
func (w *UvWorkspaceSource) Audit(
	ctx context.Context,
	// uv version (image tag) to audit with. Defaults to the version detected from the workspace; ignored when `image` is set.
	// +optional
	uvVersion *string,
	// Full uv image reference to audit with. Overrides `uv_version`.
	// +optional
	image *string,
) error {
	var resolved string
	if image != nil {
		resolved = *image
	} else {
		version := ""
		if uvVersion != nil {
			version = *uvVersion
		}
		if version == "" {
			detected, err := w.UvVersion(ctx)
			if err != nil {
				return err
			}
			version = detected
		}
		resolved = imageRef(version)
	}
	ctr := core.NewContainer().
		From(resolved).
		// the bare astral image ships uv at /uv (not on PATH); prepending /
		// makes a plain `uv` work for both it and images with uv on PATH.
		WithEnvVariable("PATH", "/:${PATH}", core.ContainerWithEnvVariableOpts{Expand: true}).
		WithDirectory("/work", w.Manifests).
		WithWorkdir(pjoin("/work", w.Path))
	_, err := ctr.WithExec([]string{"uv", "audit", "--frozen"}).Sync(ctx)
	var execErr *dagger.ExecError
	if errors.As(err, &execErr) {
		// The ExecError itself stringifies to a bare "exit code N"; fold uv's
		// report into the check's error so the findings show up there.
		return errors.New(formatAuditFailure(execErr.ExitCode, execErr.Stdout, execErr.Stderr, w.Path))
	}
	return err
}

// hasCache reports whether `path` already holds a populated uv cache in the container.
//
// A deliberately prepared base may ship a warm cache baked at `UV_CACHE_DIR`;
// mounting an empty volume over it would hide it. An absent-or-empty directory
// means there's nothing to preserve, so the caller should mount the cache volume.
func hasCache(ctx context.Context, ctr *dagger.Container, path string) bool {
	entries, err := ctr.Directory(path).Entries(ctx)
	return err == nil && len(entries) > 0
}

func hasUv(ctx context.Context, ctr *dagger.Container) (bool, error) {
	ctx, span := Tracer().Start(ctx, "detect uv on PATH")
	defer span.End()
	exitCode, err := ctr.WithExec([]string{"sh", "-c", "command -v uv"}, core.ContainerWithExecOpts{Expect: core.ReturnTypeAny}).ExitCode(ctx)
	if err != nil {
		return false, err
	}
	found := exitCode == 0
	span.SetAttributes(attribute.Bool("uv.found", found))
	return found, nil
}

// hasPython reports whether the base has its own Python that satisfies the workspace's request.
func hasPython(ctx context.Context, ctr *dagger.Container, python *string) (bool, error) {
	ctx, span := Tracer().Start(ctx, "find a system Python")
	defer span.End()
	argv := []string{"uv", "python", "find", "--system"}
	if python != nil && *python != "" {
		argv = append(argv, *python)
	}
	exitCode, err := ctr.WithExec(argv, core.ContainerWithExecOpts{Expect: core.ReturnTypeAny}).ExitCode(ctx)
	if err != nil {
		return false, err
	}
	found := exitCode == 0
	span.SetAttributes(attribute.Bool("python.found", found))
	return found, nil
}

// withProjectEnvironment chooses and prepares the environment `uv sync` installs into.
//
// Runs before the workspace's `pyproject.toml` and `uv.lock` are copied in, so
// these layers survive dependency changes. In order:
//
//   - `venvRelocatable`: a relocatable `.venv`, whatever the base sets.
//   - The base sets `UV_PROJECT_ENVIRONMENT`: uv installs there; nothing to do.
//   - The default base, or a base without a Python that satisfies `python`: a
//     uv-managed Python, installed into directly (no `.venv`). The default
//     image's `python3` belongs to Debian.
//   - A base with a suitable Python: a `.venv` on it, with a warning, as that's
//     what uv does.
func withProjectEnvironment(ctx context.Context, ctr *dagger.Container, python *string, defaultBase, venvRelocatable, activateVenv bool) (*dagger.Container, error) {
	var request []string
	if python != nil && *python != "" {
		request = []string{*python}
	}
	var pythonArgs []string
	if len(request) > 0 {
		pythonArgs = []string{"--python", *python}
	}
	switch {
	case venvRelocatable:
		ctx, span := Tracer().Start(ctx, "create relocatable .venv")
		var err error
		ctr, err = ctr.WithoutEnvVariable("UV_PROJECT_ENVIRONMENT").
			WithExec(slices.Concat([]string{"uv", "venv", "--relocatable"}, pythonArgs)).
			Sync(ctx)
		span.End()
		if err != nil {
			return nil, err
		}
	default:
		projectEnvironment, err := ctr.EnvVariable(ctx, "UV_PROJECT_ENVIRONMENT")
		if err != nil {
			return nil, err
		}
		if projectEnvironment != "" {
			return ctr, nil
		}
		managed := defaultBase
		if !managed {
			found, err := hasPython(ctx, ctr, python)
			if err != nil {
				return nil, err
			}
			managed = !found
		}
		if managed {
			return withManagedPython(ctx, ctr, request)
		}
		ctx, span := Tracer().Start(ctx, "create .venv (UV_PROJECT_ENVIRONMENT is not set)")
		fmt.Fprintln(os.Stderr, venvWarning)
		span.AddEvent("warning", trace.WithAttributes(attribute.String("message", venvWarning)))
		ctr, err = ctr.WithExec(slices.Concat([]string{"uv", "venv"}, pythonArgs)).Sync(ctx)
		span.End()
		if err != nil {
			return nil, err
		}
	}
	if activateVenv {
		workdir, err := ctr.Workdir(ctx)
		if err != nil {
			return nil, err
		}
		venv := pjoin(workdir, ".venv")
		ctr = ctr.WithEnvVariable("VIRTUAL_ENV", venv).
			WithEnvVariable("PATH", venv+"/bin:${PATH}", core.ContainerWithEnvVariableOpts{Expand: true})
	}
	return ctr, nil
}

// withManagedPython installs a uv-managed Python and points `UV_PROJECT_ENVIRONMENT` at its prefix.
func withManagedPython(ctx context.Context, ctr *dagger.Container, request []string) (*dagger.Container, error) {
	ctx, span := Tracer().Start(ctx, "install uv-managed Python")
	defer span.End()
	ctr, err := ctr.WithExec(slices.Concat([]string{"uv", "python", "install"}, request)).Sync(ctx)
	if err != nil {
		return nil, err
	}
	interpreter, err := ctr.WithExec(slices.Concat([]string{"uv", "python", "find"}, request)).Stdout(ctx)
	if err != nil {
		return nil, err
	}
	prefix, err := ctr.WithExec([]string{strings.TrimSpace(interpreter), "-c", "import sys; print(sys.prefix)"}).Stdout(ctx)
	if err != nil {
		return nil, err
	}
	prefix = strings.TrimSpace(prefix)
	span.SetAttributes(attribute.String("python.prefix", prefix))
	return ctr.WithEnvVariable("UV_PROJECT_ENVIRONMENT", prefix).
		WithEnvVariable("PATH", prefix+"/bin:${PATH}", core.ContainerWithEnvVariableOpts{Expand: true}), nil
}

// defaultBaseContainer is a Debian-based uv image pinned to this workspace's uv version.
//
// Uses the Debian variant rather than the distroless `uv:<version>` image
// because the latter is `FROM scratch`: `uv sync` can't provision a managed
// Python there (no libc). On Debian, uv downloads Python on first sync and
// ca-certificates are already present. Pass an explicit `baseContainer` to
// build on your own auth/system-package layers.
func (w *UvWorkspaceSource) defaultBaseContainer(ctx context.Context) (*dagger.Container, error) {
	version, err := w.UvVersion(ctx)
	if err != nil {
		return nil, err
	}
	return core.NewContainer().From(debianImageRef(version)).WithWorkdir("/work"), nil
}

// Arguments shared by `build`, `install` and `venv`.
type buildOptions struct {
	baseContainer   *dagger.Container
	packages        []string
	extras          []string
	groups          []string
	allExtras       bool
	allGroups       bool
	allPackages     bool
	noEditable      bool
	noDefaultGroups bool
	handlers        []BuildBackendHandler
	venvRelocatable bool
	activateVenv    bool
	autoInstallUv   bool
}

// Prepare a build for this workspace without installing anything yet.
//
// Resolves the sync plan, mounts the uv cache, prepares the environment uv
// installs into, and copies the root pyproject.toml and uv.lock into the
// container.
//
// The environment follows `UV_PROJECT_ENVIRONMENT` when the base sets it.
// Otherwise the default base, or one without a suitable Python, gets a
// uv-managed Python that packages install into directly, and a base with
// a suitable Python gets a `.venv` (activated unless `activate_venv=False`). Set
// `UV_PROJECT_ENVIRONMENT` on the base to its Python's prefix to avoid a
// redundant `.venv`.
//
// The returned `UvWorkspaceBuild` drives the pipeline from here:
// `with_remote_dependencies()` to install remote deps, `with_workspace_files()`
// to scaffold local packages, then `with_local_dependencies()` to install them.
// Skip `with_remote_dependencies()` when another tool (e.g. `pulumi install`)
// handles dependency installation.
func (w *UvWorkspaceSource) Build(ctx context.Context, args struct {
	// Container to build on top of (auth, system packages, etc.). Defaults to a Debian-based uv image at the workspace's uv version (uv provisions Python on demand).
	// +optional
	BaseContainer *dagger.Container
	// +optional
	Package []string
	// +optional
	Extra []string
	// +optional
	Group []string
	// Install every extra; maps to `uv sync --all-extras`
	// +default=false
	AllExtras bool
	// Install every dependency group; maps to `uv sync --all-groups`
	// +default=false
	AllGroups bool
	// Install every workspace member; maps to `uv sync --all-packages`
	// +default=false
	AllPackages bool
	// Install local/workspace packages non-editable (`uv sync --no-editable`), baking their source into site-packages instead of linking it. Makes the resulting venv self-contained, so it can be exported/copied (e.g. via `copy_venv`) without also carrying the workspace source.
	// +default=false
	NoEditable bool
	// Skip the default dependency groups (`[tool.uv] default-groups`, `dev` unless set); maps to `uv sync --no-default-groups`. Groups passed with `group` are still installed.
	// +default=false
	NoDefaultGroups bool
	// +optional
	BuildBackendHandlers BuildBackendHandlerList
	// Create a relocatable `.venv` (`uv venv --relocatable`) before installing, even when the base sets `UV_PROJECT_ENVIRONMENT`. Needed to copy the environment into another image (see `venv`).
	// +default=false
	VenvRelocatable bool
	// When the build uses a `.venv`, activate it in the container: set `VIRTUAL_ENV` and put its `bin/` first on `PATH`, so a plain `python` runs the installed environment.
	// +default=true
	ActivateVenv bool
	// Automatically install the uv binary if it is not already on $PATH. Set to False if your base_container already ships uv.
	// +default=true
	AutoInstallUv bool
}) (*UvWorkspaceBuild, error) {
	return w.build(ctx, buildOptions{
		baseContainer:   args.BaseContainer,
		packages:        args.Package,
		extras:          args.Extra,
		groups:          args.Group,
		allExtras:       args.AllExtras,
		allGroups:       args.AllGroups,
		allPackages:     args.AllPackages,
		noEditable:      args.NoEditable,
		noDefaultGroups: args.NoDefaultGroups,
		handlers:        args.BuildBackendHandlers,
		venvRelocatable: args.VenvRelocatable,
		activateVenv:    args.ActivateVenv,
		autoInstallUv:   args.AutoInstallUv,
	})
}

func (w *UvWorkspaceSource) build(ctx context.Context, o buildOptions) (*UvWorkspaceBuild, error) {
	ctx, span := Tracer().Start(ctx, "prepare workspace build")
	defer span.End()
	span.SetAttributes(
		attribute.String("workspace.path", w.Path),
		attribute.StringSlice("build.packages", o.packages),
		attribute.Bool("build.all_packages", o.allPackages),
		attribute.Bool("build.default_base_container", o.baseContainer == nil),
	)

	plan, err := newUvSyncPlan(ctx, syncPlanOptions{
		sourceDir:       w.Source,
		manifestsDir:    w.Manifests,
		projects:        w.Projects,
		srcInitPaths:    w.SrcInitPaths,
		workspacePath:   w.Path,
		packages:        o.packages,
		extras:          o.extras,
		groups:          o.groups,
		allExtras:       o.allExtras,
		allGroups:       o.allGroups,
		allPackages:     o.allPackages,
		noEditable:      o.noEditable,
		noDefaultGroups: o.noDefaultGroups,
		handlers:        o.handlers,
	})
	if err != nil {
		return nil, err
	}
	span.SetAttributes(
		attribute.StringSlice("uv.sync_args", plan.UvSyncArgs),
		attribute.StringSlice("build.local_packages", packageNames(plan.NeededLocal)),
	)

	ctr := o.baseContainer
	if ctr == nil {
		if ctr, err = w.defaultBaseContainer(ctx); err != nil {
			return nil, err
		}
	}
	cacheDir, err := ctr.EnvVariable(ctx, "UV_CACHE_DIR")
	if err != nil {
		return nil, err
	}
	if cacheDir != "" {
		// The base already points uv at a cache dir. Mount the cache volume there
		// unless the base actually ships a populated cache: don't shadow a warm,
		// deliberately prepared one with an empty mount. Crucially, a base built by
		// this module carries a baked UV_CACHE_DIR but *no* directory there (Dagger
		// mounts don't survive a push), so it gets re-mounted: without the mount,
		// `uv sync` would materialize the cache into a real image layer
		// (non-reproducible, ~2x size) instead of writing to the volume.
		if !hasCache(ctx, ctr, cacheDir) {
			ctr = ctr.WithMountedCache(cacheDir, core.NewCacheVolume("uv-cache"))
		}
	} else {
		cacheDir = uvCacheDir
		ctr = ctr.WithEnvVariable("UV_CACHE_DIR", cacheDir).WithMountedCache(cacheDir, core.NewCacheVolume("uv-cache"))
	}
	workdir, err := ctr.Workdir(ctx)
	if err != nil {
		return nil, err
	}

	// Place workspace files at their real path within the source tree
	// so that relative paths in uv.lock resolve correctly.
	wsCtrPath := workdir
	if w.Path != "." {
		wsCtrPath = pjoin(workdir, w.Path)
	}
	ctr = ctr.WithWorkdir(wsCtrPath)
	pythonVersion, err := w.PythonVersion(ctx)
	if err != nil {
		return nil, err
	}
	if pythonVersion != nil && *pythonVersion != "" {
		ctr = ctr.WithNewFile(pjoin(wsCtrPath, ".python-version"), *pythonVersion)
	}
	if o.autoInstallUv {
		found, err := hasUv(ctx, ctr)
		if err != nil {
			return nil, err
		}
		if !found {
			version, err := w.UvVersion(ctx)
			if err != nil {
				return nil, err
			}
			withUv, err := (&UvWorkspaceBuild{Container: ctr, Plan: plan}).WithUv(ctx, version)
			if err != nil {
				return nil, err
			}
			ctr = withUv.Container
		}
	}

	var pyprojectContents, uvLockContents string
	eg, gctx := errgroup.WithContext(ctx)
	eg.Go(func() (err error) {
		pyprojectContents, err = plan.WsDir.File("pyproject.toml").Contents(gctx)
		return err
	})
	eg.Go(func() (err error) {
		uvLockContents, err = plan.WsDir.File("uv.lock").Contents(gctx)
		return err
	})
	if err := eg.Wait(); err != nil {
		return nil, err
	}
	python, err := pythonRequest(pythonVersion, pyprojectContents)
	if err != nil {
		return nil, err
	}
	ctr, err = withProjectEnvironment(ctx, ctr, python, o.baseContainer == nil, o.venvRelocatable, o.activateVenv)
	if err != nil {
		return nil, err
	}
	ctr, err = ctr.WithNewFile(pjoin(wsCtrPath, "pyproject.toml"), pyprojectContents).
		WithNewFile(pjoin(wsCtrPath, "uv.lock"), uvLockContents).
		Sync(ctx)
	if err != nil {
		return nil, err
	}
	return &UvWorkspaceBuild{Container: ctr, Plan: plan}, nil
}

// Build a minimal container with deps installed for the given package(s).
//
// Convenience method composing `build`, `with_remote_dependencies`,
// `with_workspace_files`, and `with_local_dependencies`. For fine-grained control (e.g. running
// `pulumi install` between remote deps and local source), call them individually.
func (w *UvWorkspaceSource) Install(ctx context.Context, args struct {
	// Container to build on (auth, system packages, etc.). Defaults to a Debian-based uv image at the workspace's uv version (uv provisions Python on demand).
	// +optional
	BaseContainer *dagger.Container
	// +optional
	Package []string
	// +optional
	Extra []string
	// +optional
	Group []string
	// Install every extra; maps to `uv sync --all-extras`
	// +default=false
	AllExtras bool
	// Install every dependency group; maps to `uv sync --all-groups`
	// +default=false
	AllGroups bool
	// Install every workspace member; maps to `uv sync --all-packages`
	// +default=false
	AllPackages bool
	// Install local/workspace packages non-editable (`uv sync --no-editable`), baking their source into site-packages instead of linking it. Makes the resulting venv self-contained, so it can be exported/copied (e.g. via `copy_venv`) without also carrying the workspace source.
	// +default=false
	NoEditable bool
	// Skip the default dependency groups (`[tool.uv] default-groups`, `dev` unless set); maps to `uv sync --no-default-groups`. Groups passed with `group` are still installed.
	// +default=false
	NoDefaultGroups bool
	// +optional
	BuildBackendHandlers BuildBackendHandlerList
	// Whether to run `uv cache prune --ci` after installing remote dependencies. Learn more about the reasoning in [uv docs](https://docs.astral.sh/uv/concepts/cache/#caching-in-continuous-integration).
	// +default=true
	PruneCache bool
	// Prune the uv cache only once it grows past this many GiB.
	// +default=100
	MaxCacheSize int
	// Seconds uv waits to acquire the cache file lock (`UV_LOCK_TIMEOUT`) while pruning the cache.
	// +default=600
	LockTimeout int
	// Create a relocatable `.venv` (`uv venv --relocatable`) before installing, even when the base sets `UV_PROJECT_ENVIRONMENT`. Needed to copy the environment into another image (see `venv`).
	// +default=false
	VenvRelocatable bool
	// When the build uses a `.venv`, activate it in the container: set `VIRTUAL_ENV` and put its `bin/` first on `PATH`, so a plain `python` runs the installed environment.
	// +default=true
	ActivateVenv bool
	// Automatically install the uv binary if it is not already on $PATH. Set to False if your base_container already ships uv.
	// +default=true
	AutoInstallUv bool
}) (*dagger.Container, error) {
	b, err := w.build(ctx, buildOptions{
		baseContainer:   args.BaseContainer,
		packages:        args.Package,
		extras:          args.Extra,
		groups:          args.Group,
		allExtras:       args.AllExtras,
		allGroups:       args.AllGroups,
		allPackages:     args.AllPackages,
		noEditable:      args.NoEditable,
		noDefaultGroups: args.NoDefaultGroups,
		handlers:        args.BuildBackendHandlers,
		venvRelocatable: args.VenvRelocatable,
		activateVenv:    args.ActivateVenv,
		autoInstallUv:   args.AutoInstallUv,
	})
	if err != nil {
		return nil, err
	}
	if b, err = b.WithRemoteDependencies(ctx, args.PruneCache, args.MaxCacheSize, args.LockTimeout); err != nil {
		return nil, err
	}
	if b, err = b.WithWorkspaceFiles(ctx); err != nil {
		return nil, err
	}
	return b.WithLocalDependencies(ctx)
}

// Install into a relocatable venv and export it with the Python it links against.
//
// Like `install`, but always builds a relocatable venv and returns a `UvVenv`
// (the venv plus its uv-managed Python) that `.into(container, path)` can drop
// into any image. Use this instead of `install` when copying the
// environment into another container. Pair with `no_editable=True` so the
// exported venv carries no dependency on the workspace source.
func (w *UvWorkspaceSource) Venv(ctx context.Context, args struct {
	// Container to build on; defaults to a Debian-based uv image at the workspace's uv version.
	// +optional
	BaseContainer *dagger.Container
	// +optional
	Package []string
	// +optional
	Extra []string
	// +optional
	Group []string
	// Install every extra; maps to `uv sync --all-extras`
	// +default=false
	AllExtras bool
	// Install every dependency group; maps to `uv sync --all-groups`
	// +default=false
	AllGroups bool
	// Install every workspace member; maps to `uv sync --all-packages`
	// +default=false
	AllPackages bool
	// Install local/workspace packages non-editable (`uv sync --no-editable`), baking their source into site-packages instead of linking it. Makes the resulting venv self-contained, so it can be exported/copied (e.g. via `copy_venv`) without also carrying the workspace source.
	// +default=false
	NoEditable bool
	// Skip the default dependency groups (`[tool.uv] default-groups`, `dev` unless set); maps to `uv sync --no-default-groups`. Groups passed with `group` are still installed.
	// +default=false
	NoDefaultGroups bool
	// +optional
	BuildBackendHandlers BuildBackendHandlerList
	// Whether to run `uv cache prune --ci` after installing remote dependencies. Learn more about the reasoning in [uv docs](https://docs.astral.sh/uv/concepts/cache/#caching-in-continuous-integration).
	// +default=true
	PruneCache bool
	// Prune the uv cache only once it grows past this many GiB.
	// +default=100
	MaxCacheSize int
	// Seconds uv waits to acquire the cache file lock (`UV_LOCK_TIMEOUT`) while pruning the cache.
	// +default=600
	LockTimeout int
	// Automatically install the uv binary if it is not already on $PATH. Set to False if your base_container already ships uv.
	// +default=true
	AutoInstallUv bool
}) (*UvVenv, error) {
	b, err := w.build(ctx, buildOptions{
		baseContainer:   args.BaseContainer,
		packages:        args.Package,
		extras:          args.Extra,
		groups:          args.Group,
		allExtras:       args.AllExtras,
		allGroups:       args.AllGroups,
		allPackages:     args.AllPackages,
		noEditable:      args.NoEditable,
		noDefaultGroups: args.NoDefaultGroups,
		handlers:        args.BuildBackendHandlers,
		venvRelocatable: true,
		activateVenv:    true,
		autoInstallUv:   args.AutoInstallUv,
	})
	if err != nil {
		return nil, err
	}
	if b, err = b.WithRemoteDependencies(ctx, args.PruneCache, args.MaxCacheSize, args.LockTimeout); err != nil {
		return nil, err
	}
	if b, err = b.WithWorkspaceFiles(ctx); err != nil {
		return nil, err
	}
	ctr, err := b.WithLocalDependencies(ctx)
	if err != nil {
		return nil, err
	}
	return b.WithContainer(ctr).Venv(ctx)
}
