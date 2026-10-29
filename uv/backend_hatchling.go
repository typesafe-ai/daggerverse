package uv

// hatchling: wheel file selection, forced inclusions, hook scripts, and the version source.
//
// See https://hatch.pypa.io/latest/config/build/ and
// https://hatch.pypa.io/latest/plugins/builder/wheel/.

import "slices"

// Hatch's default script for `custom` build and metadata hooks.
const defaultHatchHookScript = "hatch_build.py"

// hatchlingSettings are the `[tool.hatch]` settings that determine the import layout and build inputs.
//
// Wheel-target options (`[tool.hatch.build.targets.wheel]`) take precedence over
// the global `[tool.hatch.build]` ones.
type hatchlingSettings struct {
	// `packages` directories; nil when unset.
	packages []string
	// `only-include` paths; nil when unset.
	onlyInclude []string
	// `bypass-selection`: the wheel ships no files of its own (e.g. a metapackage).
	bypassSelection bool
	// Package-relative sources of `force-include` mappings.
	forceInclude []string
	// Package-relative sources of the wheel's `shared-data`, `shared-scripts`, and `extra-metadata`.
	sharedPaths []string
	// Scripts of `custom` build and metadata hooks.
	hookScripts []string
	// File the `regex` or `code` version source reads (`[tool.hatch.version].path`).
	versionPath *string
}

func hatchlingFromPyproject(metadata map[string]any, name string) backendSettings {
	hatch := table(metadata, "tool", "hatch")
	build := table(hatch, "build")
	wheel := table(build, "targets", "wheel")

	option := func(key string) (any, bool) {
		if value, ok := wheel[key]; ok {
			return value, true
		}
		value, ok := build[key]
		return value, ok
	}
	paths := func(key string) []string {
		value, ok := option(key)
		if !ok {
			return nil
		}
		values, _ := stringList(value)
		return normpaths(values)
	}
	// Mapping sources in merge order: later tables override, earlier keys keep their position.
	mappingKeys := func(mappings ...map[string]any) []string {
		var result []string
		for _, mapping := range mappings {
			for _, key := range keys(mapping) {
				if !slices.Contains(result, key) {
					result = append(result, key)
				}
			}
		}
		return result
	}

	var hookScripts []string
	for _, hooks := range []map[string]any{table(build, "hooks"), table(wheel, "hooks"), table(hatch, "metadata", "hooks")} {
		hook, ok := hooks["custom"]
		if !ok || hook == nil {
			continue
		}
		script := defaultHatchHookScript
		if t, ok := hook.(map[string]any); ok {
			if p, ok := t["path"].(string); ok {
				script = p
			}
		}
		hookScripts = append(hookScripts, script)
	}
	bypass, _ := option("bypass-selection")
	bypassSelection, _ := bypass.(bool)
	return hatchlingSettings{
		packages:        paths("packages"),
		onlyInclude:     paths("only-include"),
		bypassSelection: bypassSelection,
		forceInclude:    withinPackage(mappingKeys(table(build, "force-include"), table(wheel, "force-include"))),
		sharedPaths: withinPackage(mappingKeys(
			table(wheel, "shared-data"), table(wheel, "shared-scripts"), table(wheel, "extra-metadata"),
		)),
		hookScripts: unique(hookScripts),
		versionPath: optionalString(table(hatch, "version"), "path"),
	}
}

// selection returns the explicitly selected wheel paths, or nil to leave discovery to hatch.
func (s hatchlingSettings) selection() []string {
	if s.packages == nil && s.onlyInclude == nil {
		if s.bypassSelection {
			return []string{}
		}
		return nil
	}
	return append(append([]string{}, s.packages...), s.onlyInclude...)
}

func (s hatchlingSettings) moduleName() *string { return nil }

func (s hatchlingSettings) modulePaths() []string {
	selection := s.selection()
	if selection == nil {
		return nil
	}
	return filter(selection, isModule)
}

func (s hatchlingSettings) sourcePaths() []string { return s.selection() }

func (s hatchlingSettings) buildInputs() []string {
	// Forced and shared paths may be files or directories; match both.
	inputs := []string{}
	for _, source := range slices.Concat(s.forceInclude, s.sharedPaths) {
		inputs = append(inputs, tree(source)...)
	}
	inputs = append(inputs, s.hookScripts...)
	if s.versionPath != nil && *s.versionPath != "" {
		inputs = append(inputs, *s.versionPath)
	}
	// Non-module files picked by `only-include` (e.g. a top-level data file).
	return append(inputs, filter(s.selection(), func(p string) bool { return isFile(p) && !isModule(p) })...)
}
