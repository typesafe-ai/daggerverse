package uv

import (
	"context"

	"go.opentelemetry.io/otel/attribute"
	"golang.org/x/sync/errgroup"

	"dagger/uv/internal/dagger"
)

// A single member package within a uv workspace.
//
// Carries the workspace's manifests, the workspace root path, and the
// member's own path so it can read both workspace-level and package-level
// index configuration.
type UvPackageSource struct {
	// The workspace's manifests: uv.lock, uv.toml and pyproject.toml files.
	Manifests *dagger.Directory
	// Workspace root path within the manifests (`.` for a root workspace).
	WorkspacePath string
	// Package path relative to the workspace root.
	PackagePath string
}

func (p *UvPackageSource) wsDir() *dagger.Directory {
	if p.WorkspacePath == "." {
		return p.Manifests
	}
	return p.Manifests.Directory(p.WorkspacePath)
}

func (p *UvPackageSource) pkgDir() *dagger.Directory {
	ws := p.wsDir()
	if p.PackagePath == "." {
		return ws
	}
	return ws.Directory(p.PackagePath)
}

// The package indices configured in this member's `pyproject.toml`.
//
// Reads `[[tool.uv.index]]` from the member's own `pyproject.toml`.
// When `include_from_workspace` is set, also reads workspace-level
// indices and merges them (package-level entries win on name collision).
func (p *UvPackageSource) Indices(
	ctx context.Context,
	// Merge indices from the workspace root's configuration. When True, workspace-level indices are included alongside the package's own indices (deduplicated by name, package wins).
	// +default=false
	includeFromWorkspace bool,
) ([]*UvIndex, error) {
	readPkg := func(ctx context.Context) ([]*UvIndex, error) {
		ctx, span := Tracer().Start(ctx, "read package indices")
		defer span.End()
		span.SetAttributes(attribute.String("package.path", p.PackagePath))
		content, err := p.pkgDir().File("pyproject.toml").Contents(ctx)
		if err != nil {
			return nil, err
		}
		raw, err := parseIndices(content, false)
		if err != nil {
			return nil, err
		}
		span.SetAttributes(attribute.Int("indices.count", len(raw)), attribute.StringSlice("indices.names", indexNames(raw)))
		return raw, nil
	}

	if !includeFromWorkspace {
		return readPkg(ctx)
	}

	readWs := func(ctx context.Context) ([]*UvIndex, error) {
		ctx, span := Tracer().Start(ctx, "read workspace indices")
		defer span.End()
		span.SetAttributes(attribute.String("workspace.path", p.WorkspacePath))
		raw, err := readWorkspaceIndices(ctx, p.wsDir())
		if err != nil {
			return nil, err
		}
		span.SetAttributes(attribute.Int("indices.count", len(raw)), attribute.StringSlice("indices.names", indexNames(raw)))
		return raw, nil
	}

	var pkgRaw, wsRaw []*UvIndex
	eg, gctx := errgroup.WithContext(ctx)
	eg.Go(func() (err error) {
		pkgRaw, err = readPkg(gctx)
		return err
	})
	eg.Go(func() (err error) {
		wsRaw, err = readWs(gctx)
		return err
	})
	if err := eg.Wait(); err != nil {
		return nil, err
	}

	_, span := Tracer().Start(ctx, "merge indices")
	defer span.End()
	span.SetAttributes(attribute.Int("indices.package_count", len(pkgRaw)), attribute.Int("indices.workspace_count", len(wsRaw)))
	result := mergeIndices(wsRaw, pkgRaw)
	span.SetAttributes(attribute.Int("indices.total", len(result)), attribute.StringSlice("indices.names", indexNames(result)))
	return result, nil
}

func indexNames(indices []*UvIndex) []string {
	names := make([]string, len(indices))
	for i, index := range indices {
		names[i] = index.Name
	}
	return names
}
