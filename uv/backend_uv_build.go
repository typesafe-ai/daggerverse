package uv

// uv_build: `[tool.uv.build-backend]` module names, source root, and wheel data directories.
//
// See https://docs.astral.sh/uv/concepts/build-backend/.

import "strings"

// uvBuildSettings are the `[tool.uv.build-backend]` settings that determine the import layout and build inputs.
type uvBuildSettings struct {
	// Source root; an empty string means a flat layout.
	moduleRoot string
	// Declared Python import names (dotted names map to nested directories).
	moduleNames []string
	// `[tool.uv.build-backend.data]` directories (scripts, headers, data, ...) shipped in the wheel.
	dataDirs []string
}

func uvBuildFromPyproject(metadata map[string]any, name string) backendSettings {
	backend := table(metadata, "tool", "uv", "build-backend")
	var names []string
	switch raw := backend["module-name"].(type) {
	case nil:
		names = []string{strings.NewReplacer("-", "_", ".", "_").Replace(strings.ToLower(name))}
	case string:
		names = []string{raw}
	default:
		names, _ = stringList(raw)
	}
	root := "src"
	if r, ok := backend["module-root"].(string); ok {
		root = r
	}
	root = normpath(root)
	if root == "." {
		root = ""
	}
	data := table(backend, "data")
	dataDirs := []string{}
	for _, key := range keys(data) {
		dir, _ := data[key].(string)
		dataDirs = append(dataDirs, normpath(dir))
	}
	return uvBuildSettings{moduleRoot: root, moduleNames: names, dataDirs: dataDirs}
}

func (s uvBuildSettings) moduleName() *string { return nil }

func (s uvBuildSettings) modulePaths() []string {
	paths := []string{}
	for _, name := range s.moduleNames {
		paths = append(paths, pjoin(s.moduleRoot, strings.ReplaceAll(name, ".", "/")))
	}
	return paths
}

// sourcePaths copies the source root, or only declared modules for a flat layout.
func (s uvBuildSettings) sourcePaths() []string {
	if s.moduleRoot != "" {
		return []string{s.moduleRoot}
	}
	return s.modulePaths()
}

func (s uvBuildSettings) buildInputs() []string {
	inputs := []string{}
	for _, dir := range s.dataDirs {
		inputs = append(inputs, tree(dir)...)
	}
	return inputs
}
