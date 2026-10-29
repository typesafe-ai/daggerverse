package uv

// pdm-backend: package directory, explicit includes, the custom hook, and the version source.
//
// See https://backend.pdm-project.org/build_config/ and
// https://backend.pdm-project.org/metadata/.

import "strings"

// pdm-backend runs this script as a build hook whenever it exists.
const defaultPdmCustomHook = "pdm_build.py"

// pdmSettings are the `[tool.pdm]` settings that determine the import layout and build inputs.
type pdmSettings struct {
	// Import name derived from the distribution name.
	importName string
	// `[tool.pdm.build].package-dir`; nil leaves pdm's `src/`-or-root discovery to the generic layout.
	packageDir *string
	// Literal (non-glob) `[tool.pdm.build].includes` paths; nil when unset.
	includes []string
	// `[tool.pdm.build].custom-hook` script.
	customHook string
	// File a `source = "file"` `[tool.pdm.version]` reads the version from.
	versionPath *string
}

func pdmFromPyproject(metadata map[string]any, name string) backendSettings {
	pdm := table(metadata, "tool", "pdm")
	build := table(pdm, "build")
	version := table(pdm, "version")
	var includes []string
	if raw, ok := build["includes"]; ok {
		values, _ := stringList(raw)
		// Glob includes select data inside the packages; only literal paths locate them.
		includes = normpaths(filter(values, func(p string) bool { return !strings.ContainsAny(p, "*?[") }))
	}
	var packageDir *string
	if dir := optionalString(build, "package-dir"); dir != nil {
		normalized := normpath(*dir)
		packageDir = &normalized
	}
	customHook := defaultPdmCustomHook
	if hook, ok := build["custom-hook"].(string); ok {
		customHook = hook
	}
	var versionPath *string
	if version["source"] == "file" {
		versionPath = optionalString(version, "path")
	}
	return pdmSettings{
		importName:  strings.NewReplacer("-", "_", ".", "_").Replace(strings.ToLower(name)),
		packageDir:  packageDir,
		includes:    includes,
		customHook:  customHook,
		versionPath: versionPath,
	}
}

func (s pdmSettings) moduleName() *string { return nil }

func (s pdmSettings) modulePaths() []string {
	if s.includes != nil {
		return filter(s.includes, isModule)
	}
	if s.packageDir != nil {
		return []string{normpath(pjoin(*s.packageDir, s.importName))}
	}
	return nil
}

func (s pdmSettings) sourcePaths() []string {
	if s.includes != nil {
		return s.includes
	}
	if s.packageDir != nil {
		// Copy the package root, or only the module for a flat layout.
		if *s.packageDir != "." {
			return []string{*s.packageDir}
		}
		return s.modulePaths()
	}
	return nil
}

func (s pdmSettings) buildInputs() []string {
	// The hook only runs if the script exists; a missing file is simply not staged.
	inputs := []string{s.customHook}
	if s.versionPath != nil && *s.versionPath != "" {
		inputs = append(inputs, *s.versionPath)
	}
	return append(inputs, filter(s.includes, func(p string) bool { return isFile(p) && !isModule(p) })...)
}
