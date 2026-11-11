package pyrefly

import (
	"context"
	"errors"
	"fmt"
	posixpath "path"
	"slices"
	"strings"

	"dagger.io/dagger/core"
	"golang.org/x/sync/errgroup"

	"dagger/pyrefly/internal/dagger"
	"dagger/pyrefly/internal/dagger/clients/uv"
)

// The package a uv workspace installs to be one pyrefly checks.
const toolPackage = "pyrefly"

// One uv workspace that pyrefly type checks.
type PyreflyWorkspace struct {
	// Workspace root (holding uv.lock), relative to the Dagger workspace root. `.` for the root.
	Path string

	// Every file the uv module reads to build the workspace. The uv module's own
	// objects can't be kept in a field, so the workspace is loaded again from these.
	// +private
	Inputs *dagger.Directory
	// +private
	UvCache *dagger.CacheVolume
	// +private
	Groups []string
	// +private
	Extras []string
	// +private
	AllExtras bool
	// +private
	NoInstallPackages []string
	// +private
	Args []string
	// +private
	BaseContainer *dagger.Container
}

// container returns the workspace's environment with its source, and where the workspace root is in it.
//
// Every member is installed, so pyrefly resolves the imports of all of them.
// pyrefly keeps no cache on disk; the uv cache the environment installs from
// is the uv module's cache volume.
func (m *PyreflyWorkspace) container(ctx context.Context) (*dagger.Container, string, error) {
	workspace := uv.New(m.Inputs.AsWorkspace(), uv.UvOpts{UvCache: m.UvCache}).Workspace(m.Path)
	base := m.BaseContainer
	if base != nil {
		// Keep the workspace out of the base's own working directory, which may be `/`:
		// pyrefly would then check the whole filesystem.
		base = base.WithWorkdir("/work")
	}
	builder := workspace.Builder(uv.UvWorkspaceSourceBuilderOpts{BaseContainer: base}).WithAllPackages()
	if len(m.Groups) > 0 {
		builder = builder.WithGroups(m.Groups)
	}
	if m.AllExtras {
		builder = builder.WithAllExtras()
	} else if len(m.Extras) > 0 {
		builder = builder.WithExtras(m.Extras)
	}
	if len(m.NoInstallPackages) > 0 {
		builder = builder.WithNoInstallPackages(m.NoInstallPackages)
	}
	ctr := builder.WithDependencies().Container()
	workdir, err := ctr.Workdir(ctx)
	if err != nil {
		return nil, "", err
	}
	// The build copies each member's files. Add the rest of the workspace's
	// tree, such as a `pyrefly.toml` at the root of a workspace with no root package.
	root := workdir
	if m.Path != "." {
		root = strings.TrimSuffix(workdir, "/"+m.Path)
	}
	return ctr.WithDirectory(root, workspace.Source()), workdir, nil
}

// failure folds pyrefly's report into the error of a failed exec, which otherwise reads just "exit code N".
func (m *PyreflyWorkspace) failure(err error) error {
	var exec *dagger.ExecError
	if !errors.As(err, &exec) {
		return err
	}
	var report []string
	for _, stream := range []string{exec.Stdout, exec.Stderr} {
		if text := strings.TrimSpace(stream); text != "" && !slices.Contains(report, text) {
			report = append(report, text)
		}
	}
	return fmt.Errorf("pyrefly found errors in %s (exit code %d):\n\n%s", m.Path, exec.ExitCode, strings.Join(report, "\n"))
}

// Type check this workspace with `pyrefly check`. Fails on any type error.
//
// +check
func (m *PyreflyWorkspace) Check(ctx context.Context) error {
	ctr, _, err := m.container(ctx)
	if err != nil {
		return err
	}
	_, err = ctr.WithExec(append([]string{"pyrefly", "check"}, m.Args...)).Sync(ctx)
	return m.failure(err)
}

// Record every type error pyrefly finds in the workspace's baseline file (`pyrefly check --update-baseline`).
//
// `check` then fails only on the errors that aren't in the baseline, which
// lets a codebase adopt pyrefly before its existing errors are fixed. The
// errors are those of the environment `check` runs in. Needs the `baseline`
// option set in the workspace's pyrefly configuration.
func (m *PyreflyWorkspace) UpdateBaseline(ctx context.Context) (*dagger.Changeset, error) {
	ctr, workdir, err := m.container(ctx)
	if err != nil {
		return nil, err
	}
	check := append([]string{"pyrefly", "check"}, m.Args...)
	// pyrefly exits non-zero on the errors it has just recorded. The plain check
	// after it fails if they weren't, e.g. with no baseline configured.
	updated, err := ctr.
		WithExec(append(slices.Clone(check), "--update-baseline"), dagger.ContainerWithExecOpts{Expect: dagger.ReturnTypeAny}).
		WithExec(check).
		Sync(ctx)
	if err != nil {
		return nil, m.failure(err)
	}
	return m.changes(ctr, updated, workdir), nil
}

// changes returns pyrefly's edits to the workspace: both sides come from the same container, so nothing else differs.
func (m *PyreflyWorkspace) changes(before, after *dagger.Container, workdir string) *dagger.Changeset {
	at := func(c *dagger.Container) *dagger.Directory {
		return core.NewDirectory().WithDirectory(m.Path, c.Directory(workdir), dagger.DirectoryWithDirectoryOpts{Exclude: []string{".venv"}})
	}
	return at(after).Changes(at(before))
}

// The uv workspaces pyrefly checks, keyed by root.
//
// `check` runs once per selected workspace, so each workspace is its own
// check that can be selected or skipped on its own.
//
// +collection
type PyreflyWorkspaces struct {
	// Workspace roots (holding uv.lock), relative to the Dagger workspace root. `.` for the root.
	// +keys
	Paths []string
	// +private
	Items []*PyreflyWorkspace
}

// The workspace rooted at `path`.
//
// +get
func (m *PyreflyWorkspaces) Workspace(
	// Workspace root (holding uv.lock), relative to the Dagger workspace root. `.` for the root.
	path string,
) (*PyreflyWorkspace, error) {
	for _, item := range m.Items {
		if item.Path == path {
			return item, nil
		}
	}
	return nil, fmt.Errorf("pyrefly checks no uv workspace at '%s'", path)
}

// Type check Python with [Pyrefly](https://pyrefly.org).
//
// Runs over the uv workspaces (`uv.lock`) that install `pyrefly`. Each is type
// checked in its own environment, built by the `uv` module, with the pyrefly
// version its `uv.lock` pins. Each workspace is its own check.
type Pyrefly struct {
	// +private
	Items []*PyreflyWorkspace
}

// Discovers the uv workspaces that install `pyrefly`.
//
// A workspace overrides the settings below, other than `exclude`,
// `baseContainer` and `uvCache`, in the `[tool.typesafe_daggerverse.pyrefly]`
// table of its `pyproject.toml`, with kebab-case keys such as `all-extras`.
func New(
	ctx context.Context,
	// A Dagger workspace detected from the current working directory or constructed from a Directory.
	ws *dagger.Workspace,
	// Glob patterns of workspace paths to leave out of workspaces, e.g. `**/tests/_packages/**`.
	// +optional
	exclude []string,
	// uv dependency groups to install on top of the default ones (`dev`, unless `[tool.uv] default-groups` is set). The environment must provide `pyrefly`.
	// +optional
	groups []string,
	// Extras to install when `allExtras` is off.
	// +optional
	extras []string,
	// Install every extra.
	// +default=true
	allExtras bool,
	// Packages to leave out of the environment, e.g. ones with no wheel for the platform or too heavy to install just to type check. Their imports don't resolve.
	// +optional
	noInstallPackages []string,
	// Additional arguments to pass to `pyrefly check`.
	// +optional
	args []string,
	// Container the uv workspace's environment is built on, e.g. one carrying private index credentials. Defaults to the uv module's Debian uv image.
	// +optional
	baseContainer *dagger.Container,
	// Cache volume for uv's cache, e.g. one shared with another module. Defaults to the uv module's `uv-cache`.
	// +optional
	uvCache *dagger.CacheVolume,
) (*Pyrefly, error) {
	workspaces := uv.New(ws, uv.UvOpts{Exclude: exclude, UvCache: uvCache}).Workspaces().
		Filter(uv.UvWorkspacesFilterOpts{HasPackages: []string{toolPackage}})
	paths, err := workspaces.Keys(ctx)
	if err != nil {
		return nil, err
	}
	module := settings{
		Groups:            groups,
		Extras:            extras,
		AllExtras:         allExtras,
		NoInstallPackages: noInstallPackages,
		Args:              args,
	}
	items := make([]*PyreflyWorkspace, len(paths))
	group, ctx := errgroup.WithContext(ctx)
	for i, path := range paths {
		group.Go(func() error {
			workspace := workspaces.Get(path)
			pyproject, err := ws.File(posixpath.Join(path, "pyproject.toml")).Contents(ctx)
			if err != nil {
				return err
			}
			own, err := module.override(pyproject)
			if err != nil {
				return fmt.Errorf("%s: %w", posixpath.Join(path, "pyproject.toml"), err)
			}
			items[i] = &PyreflyWorkspace{
				Path:              path,
				Inputs:            workspace.Inputs(),
				UvCache:           uvCache,
				Groups:            own.Groups,
				Extras:            own.Extras,
				AllExtras:         own.AllExtras,
				NoInstallPackages: own.NoInstallPackages,
				Args:              own.Args,
				BaseContainer:     baseContainer,
			}
			return nil
		})
	}
	if err := group.Wait(); err != nil {
		return nil, err
	}
	return &Pyrefly{Items: items}, nil
}

// The uv workspaces pyrefly checks: those that install `pyrefly`, less those matching `exclude`.
func (m *Pyrefly) Workspaces() *PyreflyWorkspaces {
	paths := make([]string, len(m.Items))
	for i, item := range m.Items {
		paths[i] = item.Path
	}
	return &PyreflyWorkspaces{Paths: paths, Items: m.Items}
}
