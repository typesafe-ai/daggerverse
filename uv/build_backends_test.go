package uv

// Backend settings determine scaffold paths and the files copied for installation.

import (
	"context"
	"fmt"
	"reflect"
	"slices"
	"strings"
	"testing"

	"dagger/uv/internal/dagger"
)

// system is the package backend for a pyproject.toml with `backend` and the
// given TOML tables, e.g. `[tool.hatch.build]` sections.
func system(t *testing.T, backend, tables, name string) packageBackend {
	t.Helper()
	pyproject := fmt.Sprintf("[build-system]\nbuild-backend = %q\n%s", backend, tables)
	metadata, err := loadsToml(pyproject)
	if err != nil {
		t.Fatal(err)
	}
	return packageBackendFromPyproject(metadata, name, nil)
}

func check[T any](t *testing.T, got, want T) {
	t.Helper()
	if !reflect.DeepEqual(got, want) {
		t.Errorf("got %#v, want %#v", got, want)
	}
}

func checkSubset(t *testing.T, got []string, want ...string) {
	t.Helper()
	for _, w := range want {
		if !slices.Contains(got, w) {
			t.Errorf("%q not in %#v", w, got)
		}
	}
}

func TestRegistrySelectsSettings(t *testing.T) {
	for backend, settings := range map[string]any{
		"uv_build":                uvBuildSettings{},
		"hatchling.build":         hatchlingSettings{},
		"pdm.backend":             pdmSettings{},
		"flit_core.buildapi":      flitSettings{},
		"poetry.core.masonry.api": poetrySettings{},
		"maturin":                 maturinSettings{},
		"setuptools.build_meta":   nil,
	} {
		b := system(t, backend, "", "example")
		check(t, *b.name, backend)
		check(t, reflect.TypeOf(b.settings), reflect.TypeOf(settings))
	}
	metadata, _ := loadsToml("[build-system]\n")
	b := packageBackendFromPyproject(metadata, "example", nil)
	check(t, b.name, (*string)(nil))
	check(t, b.settings, backendSettings(nil))
}

func TestOtherBackendUsesTheGenericLayout(t *testing.T) {
	b := system(t, "setuptools.build_meta", "", "example")
	check(t, b.modulePaths(), []string(nil))
	check(t, b.sourcePaths(), []string(nil))
	check(t, b.buildInputs(), []string{})
}

// ---- [project] inputs ----

func TestProjectInputs(t *testing.T) {
	for project, want := range map[string][]string{
		"": {},
		"license-files = [\"LICEN[CS]E*\"]\nreadme = \"docs/PYPI_README.md\"": {"LICEN[CS]E*", "docs/PYPI_README.md"},
		"readme = { file = \"README.rst\", content-type = \"text/x-rst\" }":   {"README.rst"},
		"readme = { text = \"inline\", content-type = \"text/plain\" }":       {},
		"license = { file = \"COPYING\" }":                                    {"COPYING"},
		"license = \"MIT\"":                                                   {},
	} {
		check(t, system(t, "setuptools.build_meta", "[project]\n"+project, "example").buildInputs(), want)
	}
}

func TestBuildInputsDeduplicated(t *testing.T) {
	b := system(t, "hatchling.build", `
[project]
license-files = ["LICENSE"]
readme = "pkg/__init__.py"
[tool.hatch.version]
path = "pkg/__init__.py"
`, "example")
	check(t, b.buildInputs(), []string{"LICENSE", "pkg/__init__.py"})
}

// ---- uv_build ----

func uvBuild(t *testing.T, settings, name string) packageBackend {
	return system(t, "uv_build", "[tool.uv.build-backend]\n"+settings, name)
}

func TestUvBuildFlatRootsCopyOnlyDeclaredModules(t *testing.T) {
	for _, root := range []string{"", ".", "./"} {
		b := uvBuild(t, fmt.Sprintf("module-name = [\"actual_package\", \"compat_package\"]\nmodule-root = %q\n", root), "example")
		check(t, b.settings.(uvBuildSettings).moduleRoot, "")
		check(t, b.modulePaths(), []string{"actual_package", "compat_package"})
		check(t, b.sourcePaths(), []string{"actual_package", "compat_package"})
	}
}

func TestUvBuildDefaultModuleNameAndSourceRoot(t *testing.T) {
	b := uvBuild(t, "", "Example.Dist-Name")
	check(t, b.modulePaths(), []string{"src/example_dist_name"})
	check(t, b.sourcePaths(), []string{"src"})
}

func TestUvBuildDottedModuleUnderCustomRoot(t *testing.T) {
	b := uvBuild(t, "module-name = \"example.api\"\nmodule-root = \"./python\"\n", "example")
	check(t, b.modulePaths(), []string{"python/example/api"})
	check(t, b.sourcePaths(), []string{"python"})
}

func TestUvBuildDataDirsAreBuildInputs(t *testing.T) {
	b := uvBuild(t, "data = { scripts = \"bin\", headers = \"./include\" }\n", "example")
	check(t, b.buildInputs(), []string{"bin", "bin/**", "include", "include/**"})
}

// ---- hatchling ----

func hatchling(t *testing.T, hatch string) packageBackend {
	return system(t, "hatchling.build", hatch, "example")
}

func TestHatchlingWithoutSelectionUsesTheGenericLayout(t *testing.T) {
	b := hatchling(t, "")
	check(t, b.modulePaths(), []string(nil))
	check(t, b.sourcePaths(), []string(nil))
	check(t, b.buildInputs(), []string{})
}

func TestHatchlingWheelPackages(t *testing.T) {
	b := hatchling(t, "[tool.hatch.build.targets.wheel]\npackages = [\"src/openai\", \"./lib/extra\"]\n")
	check(t, b.modulePaths(), []string{"src/openai", "lib/extra"})
	check(t, b.sourcePaths(), []string{"src/openai", "lib/extra"})
}

func TestHatchlingWheelOptionsOverrideGlobalBuildOptions(t *testing.T) {
	b := hatchling(t, "[tool.hatch.build]\npackages = [\"src/a\"]\n[tool.hatch.build.targets.wheel]\npackages = [\"src/b\"]\n")
	check(t, b.modulePaths(), []string{"src/b"})
}

func TestHatchlingGlobalBuildPackages(t *testing.T) {
	check(t, hatchling(t, "[tool.hatch.build]\npackages = [\"src/a\"]\n").modulePaths(), []string{"src/a"})
}

func TestHatchlingOnlyIncludeFilesAndDirectories(t *testing.T) {
	b := hatchling(t, "[tool.hatch.build.targets.wheel]\nonly-include = [\"src/pkg\", \"single.py\", \"data.json\"]\n")
	// Modules are stubbed; a non-module file is staged as-is and copied with the sources.
	check(t, b.modulePaths(), []string{"src/pkg", "single.py"})
	check(t, b.sourcePaths(), []string{"src/pkg", "single.py", "data.json"})
	check(t, b.buildInputs(), []string{"data.json"})
}

func TestHatchlingPackagesAndOnlyIncludeCombine(t *testing.T) {
	b := hatchling(t, "[tool.hatch.build]\npackages = [\"src/pkg\"]\nonly-include = [\"extra\"]\n")
	check(t, b.modulePaths(), []string{"src/pkg", "extra"})
}

func TestHatchlingBypassSelectionOrEmptyPackagesShipNoModules(t *testing.T) {
	for _, selection := range []string{"", "packages = []\n"} {
		b := hatchling(t, "[tool.hatch.build.targets.wheel]\nbypass-selection = true\n"+selection)
		check(t, b.modulePaths(), []string{})
		check(t, b.sourcePaths(), []string{})
	}
}

func TestHatchlingEmptyPackagesShipNoModules(t *testing.T) {
	check(t, hatchling(t, "[tool.hatch.build]\npackages = []\n").modulePaths(), []string{})
}

func TestHatchlingForceIncludeAndSharedSourcesInsideThePackage(t *testing.T) {
	b := hatchling(t, `
[tool.hatch.build.force-include]
assets = "pkg/assets"
"../outside" = "pkg/outside"
"/abs/x" = "pkg/x"
[tool.hatch.build.targets.wheel.force-include]
"CHANGELOG.md" = "pkg/CHANGELOG.md"
[tool.hatch.build.targets.wheel.shared-data]
"share/man" = "share/man"
[tool.hatch.build.targets.wheel.shared-scripts]
bin = "bin"
[tool.hatch.build.targets.wheel.extra-metadata]
"NOTICE.txt" = "NOTICE.txt"
`)
	check(t, b.buildInputs(), []string{
		"assets", "assets/**", "CHANGELOG.md", "share/man", "share/man/**", "bin", "bin/**", "NOTICE.txt",
	})
}

func TestHatchlingCustomHookScripts(t *testing.T) {
	b := hatchling(t, `
[tool.hatch.build.hooks.custom]
[tool.hatch.build.targets.wheel.hooks.custom]
path = "build/wheel.py"
[tool.hatch.metadata.hooks.custom]
path = "scripts/hatch_metadata.py"
`)
	check(t, b.buildInputs(), []string{"hatch_build.py", "build/wheel.py", "scripts/hatch_metadata.py"})
}

func TestHatchlingNonCustomHooksAddNoScripts(t *testing.T) {
	b := hatchling(t, "[tool.hatch.metadata.hooks.fancy-pypi-readme]\n[tool.hatch.build.hooks.vcs]\n")
	check(t, b.buildInputs(), []string{})
}

func TestHatchlingVersionPathIsABuildInput(t *testing.T) {
	b := hatchling(t, "[tool.hatch.version]\npath = \"src/pkg/__about__.py\"\n")
	check(t, b.buildInputs(), []string{"src/pkg/__about__.py"})
}

// ---- pdm-backend ----

func pdm(t *testing.T, tables, name string) packageBackend {
	return system(t, "pdm.backend", tables, name)
}

func TestPdmDefaultsUseTheGenericLayoutAndStageTheDefaultHook(t *testing.T) {
	b := pdm(t, "", "example")
	check(t, b.modulePaths(), []string(nil))
	check(t, b.sourcePaths(), []string(nil))
	check(t, b.buildInputs(), []string{"pdm_build.py"})
}

func TestPdmVersionFileIsABuildInput(t *testing.T) {
	for version, want := range map[string][]string{
		"source = \"file\"\npath = \"fastapi/__init__.py\"\n": {"pdm_build.py", "fastapi/__init__.py"},
		"source = \"scm\"\n": {"pdm_build.py"},
	} {
		check(t, pdm(t, "[tool.pdm.version]\n"+version, "example").buildInputs(), want)
	}
}

func TestPdmPackageDir(t *testing.T) {
	for _, tc := range []struct {
		packageDir               string
		modulePaths, sourcePaths []string
	}{
		{"src", []string{"src/my_pkg"}, []string{"src"}},
		{"./lib/", []string{"lib/my_pkg"}, []string{"lib"}},
		{".", []string{"my_pkg"}, []string{"my_pkg"}},
	} {
		b := pdm(t, fmt.Sprintf("[tool.pdm.build]\npackage-dir = %q\n", tc.packageDir), "My-Pkg")
		check(t, b.modulePaths(), tc.modulePaths)
		check(t, b.sourcePaths(), tc.sourcePaths)
	}
}

func TestPdmIncludesIgnoreGlobsAndSplitModulesFromFiles(t *testing.T) {
	b := pdm(t, "[tool.pdm.build]\nincludes = [\"src/pkg/\", \"src/pkg/**/*.json\", \"tool.py\", \"LICENSE.txt\"]\n", "example")
	check(t, b.modulePaths(), []string{"src/pkg", "tool.py"})
	check(t, b.sourcePaths(), []string{"src/pkg", "tool.py", "LICENSE.txt"})
	check(t, b.buildInputs(), []string{"pdm_build.py", "LICENSE.txt"})
}

func TestPdmCustomHookPath(t *testing.T) {
	check(t, pdm(t, "[tool.pdm.build]\ncustom-hook = \"build/hook.py\"\n", "example").buildInputs(), []string{"build/hook.py"})
}

// ---- flit_core ----

func flit(t *testing.T, tables string) packageBackend {
	return system(t, "flit_core.buildapi", tables, "example-dist")
}

func TestFlitDefaultModuleNameUsesTheGenericLayout(t *testing.T) {
	b := flit(t, "")
	check(t, *b.moduleName(), "example_dist")
	check(t, b.modulePaths(), []string(nil))
	check(t, b.sourcePaths(), []string(nil))
	check(t, b.buildInputs(), []string{})
}

func TestFlitDottedModuleName(t *testing.T) {
	check(t, *flit(t, "[tool.flit.module]\nname = \"airflow.providers.smtp\"\n").moduleName(), "airflow.providers.smtp")
}

func TestFlitDynamicMetadataStagesTheModule(t *testing.T) {
	for _, dynamic := range []string{`["version"]`, `["description"]`, `["version", "description"]`} {
		b := flit(t, "[project]\ndynamic = "+dynamic+"\n[tool.flit.module]\nname = \"ns.pkg\"\n")
		check(t, b.buildInputs(), []string{"ns/pkg/__init__.py", "src/ns/pkg/__init__.py"})
	}
}

func TestFlitStaticMetadataStagesNothing(t *testing.T) {
	b := flit(t, "[project]\nversion = \"1.0\"\ndescription = \"x\"\ndynamic = [\"readme\"]\n")
	check(t, b.buildInputs(), []string{})
}

func TestFlitExternalData(t *testing.T) {
	check(t, flit(t, "[tool.flit.external-data]\ndirectory = \"data\"\n").buildInputs(), []string{"data", "data/**"})
}

func TestOnlyFlitDeclaresAModuleName(t *testing.T) {
	for _, backend := range []string{"uv_build", "hatchling.build", "pdm.backend", "poetry.core.masonry.api", "setuptools.build_meta"} {
		check(t, system(t, backend, "", "example").moduleName(), (*string)(nil))
	}
}

// ---- poetry-core ----

func poetry(t *testing.T, tables string) packageBackend {
	return system(t, "poetry.core.masonry.api", "[tool.poetry]\n"+tables, "example")
}

func TestPoetryDefaultsUseTheGenericLayout(t *testing.T) {
	b := poetry(t, "")
	check(t, b.modulePaths(), []string(nil))
	check(t, b.sourcePaths(), []string(nil))
	check(t, b.buildInputs(), []string{})
}

func TestPoetryPackagesJoinFromAndSkipSdistOnlyEntries(t *testing.T) {
	b := poetry(t, `packages = [
  { include = "cleo", from = "src" },
  { include = "extra/**/*.py" },
  { include = "docs_only", format = "sdist" },
  { include = "both", format = ["sdist", "wheel"] },
  { include = "single.py" },
]
`)
	want := []string{"src/cleo", "extra", "both", "single.py"}
	check(t, b.modulePaths(), want)
	check(t, b.sourcePaths(), want)
}

func TestPoetryEmptyPackagesShipNoModules(t *testing.T) {
	check(t, poetry(t, "packages = []\n").modulePaths(), []string{})
}

func TestPoetryWheelIncludesReadmeAndBuildScript(t *testing.T) {
	b := poetry(t, `readme = ["README.md", "CHANGELOG.md"]
include = ["pkg/py.typed", "data", "*.cfg", { path = "tests", format = "sdist" }]
build = { script = "build.py" }
`)
	check(t, b.buildInputs(), []string{"pkg/py.typed", "data", "data/**", "*.cfg", "README.md", "CHANGELOG.md", "build.py"})
}

func TestPoetryLegacyBuildString(t *testing.T) {
	check(t, poetry(t, "build = \"build.py\"\n").buildInputs(), []string{"build.py"})
}

// ---- handlers ----

// setuptoolsHandler is another module's handler, as the interface sees it.
type setuptoolsHandler struct{ DaggerObject }

func (setuptoolsHandler) BuildBackend(context.Context) (string, error) {
	return "setuptools.build_meta", nil
}

func (setuptoolsHandler) ModuleName(context.Context, string, string) (string, error) {
	return "", nil
}

func (setuptoolsHandler) ModulePaths(_ context.Context, _ string, name string) ([]string, error) {
	return []string{"lib/" + name}, nil
}

func (setuptoolsHandler) SourcePaths(context.Context, string, string) ([]string, error) {
	return []string{"lib"}, nil
}

func (setuptoolsHandler) BuildInputs(context.Context, string, string) ([]string, error) {
	return []string{"VERSION"}, nil
}

func (setuptoolsHandler) Setup(container *dagger.Container) *dagger.Container    { return container }
func (setuptoolsHandler) Teardown(container *dagger.Container) *dagger.Container { return container }

func TestHandlerLayoutResolvesEveryAnswer(t *testing.T) {
	layout, err := resolveBuildBackendLayout(t.Context(), setuptoolsHandler{}, "", "plugged")
	if err != nil {
		t.Fatal(err)
	}
	check(t, layout.ModuleName, (*string)(nil))
	check(t, layout.ModulePaths, []string{"lib/plugged"})
	check(t, layout.SourcePaths, []string{"lib"})
	check(t, layout.BuildInputs, []string{"VERSION"})
}

func TestHandlerLayoutReplacesBuiltinSettings(t *testing.T) {
	for _, backend := range []string{"setuptools.build_meta", "hatchling.build"} {
		layout := &BuildBackendLayout{ModulePaths: []string{"lib/plugged"}, SourcePaths: []string{"lib"}, BuildInputs: []string{"VERSION"}}
		metadata, _ := loadsToml(fmt.Sprintf("[build-system]\nbuild-backend = %q\n[project]\nreadme = \"README.md\"\n", backend))
		b := packageBackendFromPyproject(metadata, "plugged", layout)
		if b.settings != backendSettings(layout) {
			t.Errorf("settings %#v aren't the layout", b.settings)
		}
		check(t, b.modulePaths(), []string{"lib/plugged"})
		check(t, b.sourcePaths(), []string{"lib"})
		check(t, b.buildInputs(), []string{"README.md", "VERSION"})
	}
}

// The engine turns a handler's nil list into an empty one; a built-in backend's
// handler still falls back to the generic layout where the built-in support does.
func TestBuiltinHandlerLayoutFallsBackLikeTheBuiltinSupport(t *testing.T) {
	metadata, _ := loadsToml("[build-system]\nbuild-backend = \"hatchling.build\"\n")
	layout := fallBackLikeBuiltin(&BuildBackendLayout{ModulePaths: []string{}, SourcePaths: []string{}}, metadata, "example")
	check(t, layout.ModulePaths, []string(nil))
	check(t, layout.SourcePaths, []string(nil))

	metadata, _ = loadsToml("[build-system]\nbuild-backend = \"maturin\"\n")
	layout = fallBackLikeBuiltin(&BuildBackendLayout{ModulePaths: []string{}, SourcePaths: []string{}}, metadata, "example")
	check(t, layout.ModulePaths, []string{})
	check(t, layout.SourcePaths, []string{})

	metadata, _ = loadsToml("[build-system]\nbuild-backend = \"setuptools.build_meta\"\n")
	layout = fallBackLikeBuiltin(&BuildBackendLayout{ModulePaths: []string{}}, metadata, "example")
	check(t, layout.ModulePaths, []string{})
}

// ---- maturin ----

func maturin(t *testing.T, tables, name string) packageBackend {
	return system(t, "maturin", tables, name)
}

func TestMaturinPureRustStagesRustAndStubsOnly(t *testing.T) {
	b := maturin(t, "[tool.maturin]\nmodule-name = \"jiter\"\n", "jiter")
	check(t, b.modulePaths(), []string{})
	check(t, b.sourcePaths(), []string{})
	checkSubset(t, b.buildInputs(), "Cargo.toml", "Cargo.lock", "src/**", "build.rs")
	for _, input := range b.buildInputs() {
		if strings.HasPrefix(input, "/") {
			t.Errorf("%q is workspace-root-relative", input)
		}
	}
	checkSubset(t, b.buildInputs(), "jiter.pyi", "py.typed")
}

func TestMaturinDefaultModuleNameIsPureRust(t *testing.T) {
	check(t, maturin(t, "", "example-dist").modulePaths(), []string{})
}

func TestMaturinDottedModuleIsAMixedPackageAtTheRoot(t *testing.T) {
	b := maturin(t, "[tool.maturin]\nmodule-name = \"watchfiles._rust_notify\"\n", "example-dist")
	check(t, b.modulePaths(), []string{"watchfiles"})
	check(t, b.sourcePaths(), []string{"watchfiles"})
	if slices.Contains(b.buildInputs(), "watchfiles._rust_notify.pyi") {
		t.Error("a mixed package stages the native module's stubs")
	}
}

func TestMaturinPythonSource(t *testing.T) {
	b := maturin(t, "[tool.maturin]\nmodule-name = \"pkg._native\"\npython-source = \"python\"\n", "example-dist")
	check(t, b.modulePaths(), []string{"python/pkg"})
	check(t, b.sourcePaths(), []string{"python"})
}

func TestMaturinCargoMetadataFilesAndWheelIncludes(t *testing.T) {
	b := maturin(t, `
[project]
dynamic = ["version", "readme"]
[tool.maturin]
include = ["data/schema.json", { path = "sdist-only", format = "sdist" }, { path = "assets" }]
`, "example-dist")
	inputs := b.buildInputs()
	checkSubset(t, inputs, "README*", "data/schema.json", "assets", "assets/**")
	if slices.Contains(inputs, "LICENSE*") {
		t.Error("LICENSE* staged without a dynamic license")
	}
	for _, input := range inputs {
		if strings.HasPrefix(input, "sdist-only") {
			t.Errorf("sdist-only include %q staged", input)
		}
	}
}

func TestMaturinCrateInputsFollowTheManifestPath(t *testing.T) {
	b := maturin(t, "[tool.maturin]\nmodule-name = \"pkg._native\"\nmanifest-path = \"rust/Cargo.toml\"\n", "example-dist")
	checkSubset(t, b.buildInputs(), "rust/Cargo.toml", "rust/Cargo.lock", "rust/src/**", "rust/build.rs")
}

// ---- handler selection ----

func TestHandlersByBackendIndexesEachBackend(t *testing.T) {
	byBackend, err := handlersByBackend([]string{"maturin", "hatchling.build"})
	check(t, err, nil)
	check(t, byBackend, map[string]int{"maturin": 0, "hatchling.build": 1})
	byBackend, _ = handlersByBackend([]string{})
	check(t, byBackend, map[string]int{})
}

func TestHandlersByBackendRejectsTwoHandlersForOneBackend(t *testing.T) {
	_, err := handlersByBackend([]string{"maturin", "hatchling.build", "maturin"})
	if err == nil || !strings.Contains(err.Error(), "more than one build backend handler for maturin") {
		t.Errorf("unexpected error %v", err)
	}
}

func TestBuiltinHandlerAddsPackageConfigToTheBuiltinAnswers(t *testing.T) {
	pyproject := "[build-system]\nbuild-backend = \"maturin\"\n[tool.maturin]\nmodule-name = \"jiter\"\n"
	handler := (&MaturinHandler{}).WithPackageConfig("Jiter", []string{"/Cargo.lock"})
	check(t, handler.BuildBackend(), "maturin")
	modulePaths, _ := handler.ModulePaths(pyproject, "jiter")
	check(t, modulePaths, []string{})
	jiter, _ := handler.BuildInputs(pyproject, "jiter")
	checkSubset(t, jiter, "Cargo.toml")
	check(t, jiter[len(jiter)-1], "/Cargo.lock")
	other, _ := handler.BuildInputs(pyproject, "other")
	if slices.Contains(other, "/Cargo.lock") {
		t.Error("another package got jiter's extra inputs")
	}
}

func TestBuiltinHandlerPackageConfigsAccumulate(t *testing.T) {
	handler := (&MaturinHandler{}).
		WithPackageConfig("a", []string{"a.txt"}).
		WithPackageConfig("b", []string{"b.txt"})
	check(t, handler.PackageInputs, [][]string{{"a", "a.txt"}, {"b", "b.txt"}})
}
