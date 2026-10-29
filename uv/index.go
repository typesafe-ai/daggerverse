package uv

import (
	"context"
	"slices"
	"strings"

	"dagger/uv/internal/dagger"
)

// A configured package index for a uv workspace.
type UvIndex struct {
	// Index name.
	Name string
	// Index URL (used for resolving and downloading packages).
	Url string
	// URL to use when publishing packages to this index (defaults to `url` if unset).
	PublishUrl *string
	// When true, this index replaces PyPI as the default index.
	Default bool
	// When true, packages are only installed from this index if explicitly pinned to it.
	Explicit bool
	// Credential handling: 'always', 'never', or null (try unauthenticated first, then authenticate).
	Authenticate *string
	// Index format: 'flat' for flat directories/HTML lists, null for standard PEP 503 registries.
	Format *string
}

// mergeIndices merges two index lists, deduplicating by name (override wins).
func mergeIndices(base, override []*UvIndex) []*UvIndex {
	byName := map[string]*UvIndex{}
	for _, entry := range slices.Concat(base, override) {
		byName[entry.Name] = entry
	}
	merged := make([]*UvIndex, 0, len(byName))
	for _, name := range sortedKeys(byName) {
		merged = append(merged, byName[name])
	}
	slices.SortStableFunc(merged, func(a, b *UvIndex) int { return strings.Compare(a.Name, b.Name) })
	return merged
}

// readWorkspaceIndices reads index entries from workspace-level config (uv.toml or pyproject.toml).
func readWorkspaceIndices(ctx context.Context, wsDir *dagger.Directory) ([]*UvIndex, error) {
	entries, err := wsDir.Entries(ctx)
	if err != nil {
		return nil, err
	}
	name, uvToml := "pyproject.toml", false
	if slices.Contains(entries, "uv.toml") {
		name, uvToml = "uv.toml", true
	}
	content, err := wsDir.File(name).Contents(ctx)
	if err != nil {
		return nil, err
	}
	return parseIndices(content, uvToml)
}
