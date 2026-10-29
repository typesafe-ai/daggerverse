package uv

// poetry-core: `[tool.poetry]` packages, included files, the legacy readme, and the build script.
//
// See https://python-poetry.org/docs/pyproject/.

import "strings"

const globChars = "*?["

// inWheel reports whether a `packages`/`include` entry ships in wheels (no `format` means every format).
func inWheel(entry any) bool {
	t, ok := entry.(map[string]any)
	if !ok {
		return true
	}
	formats, ok := t["format"]
	if !ok || formats == nil {
		return true
	}
	if s, ok := formats.(string); ok {
		return s == "wheel"
	}
	list, _ := stringList(formats)
	for _, f := range list {
		if f == "wheel" {
			return true
		}
	}
	return false
}

// literalPrefix returns the leading path components before the first glob component.
func literalPrefix(p string) string {
	var parts []string
	for part := range strings.SplitSeq(p, "/") {
		if strings.ContainsAny(part, globChars) {
			break
		}
		parts = append(parts, part)
	}
	return strings.Join(parts, "/")
}

// poetrySettings are the `[tool.poetry]` settings that determine the import layout and build inputs.
type poetrySettings struct {
	// Wheel `packages` (`from` joined with `include`); glob entries are cut to their
	// literal leading directory. nil leaves poetry's discovery to the generic layout.
	packages []string
	// Wheel `include` paths and globs (entries without a `format` count, too).
	includes []string
	// The legacy `[tool.poetry].readme` file(s), used when there is no `[project]` table.
	readme []string
	// The `[tool.poetry.build]` script (`build = "..."` in older projects).
	buildScript *string
}

func poetryFromPyproject(metadata map[string]any, name string) backendSettings {
	poetry := table(metadata, "tool", "poetry")
	var packages []string
	if raw, ok := poetry["packages"]; ok {
		packages = []string{}
		for _, entry := range tables(raw) {
			if !inWheel(entry) {
				continue
			}
			from, _ := entry["from"].(string)
			include, _ := entry["include"].(string)
			if p := literalPrefix(pjoin(from, include)); p != "" {
				packages = append(packages, normpath(p))
			}
		}
		packages = unique(packages)
	}
	includes := []string{}
	if raw, ok := poetry["include"]; ok {
		for _, entry := range anyList(raw) {
			if !inWheel(entry) {
				continue
			}
			if t, ok := entry.(map[string]any); ok {
				entry = t["path"]
			}
			s, _ := entry.(string)
			includes = append(includes, s)
		}
	}
	var readme []string
	switch r := poetry["readme"].(type) {
	case string:
		readme = []string{r}
	default:
		readme, _ = stringList(r)
	}
	var buildScript *string
	switch build := poetry["build"].(type) {
	case map[string]any:
		buildScript = optionalString(build, "script")
	case string:
		buildScript = &build
	}
	return poetrySettings{packages: packages, includes: includes, readme: readme, buildScript: buildScript}
}

func (s poetrySettings) moduleName() *string { return nil }

func (s poetrySettings) modulePaths() []string {
	if s.packages == nil {
		return nil
	}
	return filter(s.packages, isModule)
}

func (s poetrySettings) sourcePaths() []string { return s.packages }

func (s poetrySettings) buildInputs() []string {
	// A literal include may be a file or a directory; a glob is matched as written.
	inputs := []string{}
	for _, include := range s.includes {
		if strings.ContainsAny(include, globChars) {
			inputs = append(inputs, include)
		} else {
			inputs = append(inputs, tree(include)...)
		}
	}
	inputs = append(inputs, s.readme...)
	if s.buildScript != nil && *s.buildScript != "" {
		inputs = append(inputs, *s.buildScript)
	}
	return append(inputs, filter(s.packages, func(p string) bool { return isFile(p) && !isModule(p) })...)
}
