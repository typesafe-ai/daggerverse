package uv

// flit_core: the import name, metadata read from the module, and external data.
//
// See https://flit.pypa.io/en/stable/pyproject_toml.html.

import "strings"

// flitSettings are the `[tool.flit]` settings that determine the import name and build inputs.
//
// flit finds the module under `src/` or at the package root, so the location is
// left to the generic layout; only the name comes from the settings.
type flitSettings struct {
	// `[tool.flit.module].name`, dotted for a module inside namespace packages.
	module string
	// The version or description is dynamic, so flit reads the module's docstring and `__version__`.
	readsModule bool
	// `[tool.flit.external-data].directory`, shipped in the wheel's data.
	externalData *string
}

func flitFromPyproject(metadata map[string]any, name string) backendSettings {
	flit := table(metadata, "tool", "flit")
	module, ok := table(flit, "module")["name"].(string)
	if !ok {
		module = strings.ReplaceAll(name, "-", "_")
	}
	dynamic, _ := stringList(table(metadata, "project")["dynamic"])
	readsModule := false
	for _, field := range dynamic {
		// `[project].dynamic` fields flit reads from the module itself.
		if field == "version" || field == "description" {
			readsModule = true
		}
	}
	return flitSettings{
		module:       module,
		readsModule:  readsModule,
		externalData: optionalString(table(flit, "external-data"), "directory"),
	}
}

func (s flitSettings) moduleName() *string   { return &s.module }
func (s flitSettings) modulePaths() []string { return nil }
func (s flitSettings) sourcePaths() []string { return nil }

func (s flitSettings) buildInputs() []string {
	inputs := []string{}
	if s.readsModule {
		// The package sits at the root or under `src/`; only the one that exists is staged.
		module := strings.ReplaceAll(s.module, ".", "/")
		inputs = append(inputs, module+"/__init__.py", "src/"+module+"/__init__.py")
	}
	if s.externalData != nil && *s.externalData != "" {
		inputs = append(inputs, tree(*s.externalData)...)
	}
	return inputs
}
