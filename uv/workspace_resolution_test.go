package uv

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

const fixtures = "tests/_packages"

func loadLock(t *testing.T, path string) map[string]any {
	t.Helper()
	content, err := os.ReadFile(filepath.Join(path, "uv.lock"))
	if err != nil {
		t.Fatal(err)
	}
	data, err := loadsToml(string(content))
	if err != nil {
		t.Fatal(err)
	}
	return data
}

func exists(path string) bool {
	_, err := os.Stat(path)
	return err == nil
}

// isFlatPackage replicates the flat-package detection from newUvSyncPlan.
func isFlatPackage(t *testing.T, wsRoot string, local orderedPackages, pkg string) bool {
	t.Helper()
	path, ok := local[pkg]
	if !ok {
		return false
	}
	content, err := os.ReadFile(filepath.Join(wsRoot, path, "pyproject.toml"))
	if err != nil {
		t.Fatal(err)
	}
	metadata, _ := loadsToml(string(content))
	_, hasBuildSystem := metadata["build-system"]
	return !hasBuildSystem
}

// Tests using a workspace with my-app -> my-lib -> my-core.
func TestWorkspace(t *testing.T) {
	lock := loadLock(t, fixtures+"/workspace")
	local := parseLocalPackages(lock)
	check(t, local, orderedPackages{"my-app": "my-app", "my-lib": "my-lib", "my-core": "my-core", "test-ws": "."})
	// Virtual metadata must be available when staging the workspace.
	check(t, local["test-ws"], ".")
	check(t, sortedKeys(local), []string{"my-app", "my-core", "my-lib", "test-ws"})

	fromApp := findTransitiveLocalDeps(lock, "my-app", nil)
	check(t, fromApp, orderedPackages{"my-app": "my-app", "my-lib": "my-lib", "my-core": "my-core"})
	check(t, sortedKeys(fromApp), []string{"my-app", "my-core", "my-lib"})
	fromLib := findTransitiveLocalDeps(lock, "my-lib", nil)
	check(t, fromLib, orderedPackages{"my-lib": "my-lib", "my-core": "my-core"})
	check(t, sortedKeys(fromLib), []string{"my-core", "my-lib"})
	check(t, findTransitiveLocalDeps(lock, "my-core", nil), orderedPackages{"my-core": "my-core"})
}

// Tests using a workspace where some local deps don't exist in the source tree.
func TestPartialWorkspace(t *testing.T) {
	lock := loadLock(t, fixtures+"/partial-workspace/sub-project")
	check(t, parseLocalPackages(lock), orderedPackages{"ext-pkg": "../../gone/ext-pkg", "my-dep": "../my-dep", "sub-project": "."})
	result := findTransitiveLocalDeps(lock, "sub-project", nil)
	if _, ok := result["ext-pkg"]; !ok {
		t.Error("ext-pkg missing")
	}
	if _, ok := result["my-dep"]; !ok {
		t.Error("my-dep missing")
	}
	check(t, findTransitiveLocalDeps(lock, "my-dep", nil), orderedPackages{"my-dep": "../my-dep"})
}

// A virtual workspace root (source = virtual) still pulls in its members.
//
// Virtual members must be scaffolded and traversed, including when their
// dependencies are reached through another virtual member.
func TestVirtualWorkspaceRoot(t *testing.T) {
	lock, err := loadsToml(`
[[package]]
name = "the-root"
source = { virtual = "." }
dependencies = [{ name = "alarms" }, { name = "networking" }]
[[package]]
name = "alarms"
source = { virtual = "alarms" }
dependencies = [{ name = "networking" }]
[[package]]
name = "networking"
source = { editable = "networking" }
`)
	if err != nil {
		t.Fatal(err)
	}
	all := orderedPackages{"the-root": ".", "alarms": "alarms", "networking": "networking"}
	check(t, parseLocalPackages(lock), all)
	check(t, findTransitiveLocalDeps(lock, "the-root", nil), all)
	check(t, findTransitiveLocalDeps(lock, "alarms", nil), orderedPackages{"alarms": "alarms", "networking": "networking"})
}

func TestStandalone(t *testing.T) {
	lock := loadLock(t, fixtures+"/standalone-app")
	check(t, parseLocalPackages(lock), orderedPackages{"standalone-app": "."})
	check(t, findTransitiveLocalDeps(lock, "standalone-app", nil), orderedPackages{"standalone-app": "."})
	check(t, findTransitiveLocalDeps(lock, "nonexistent", nil), orderedPackages{})
}

// Tests using a workspace where my-app is a flat app (no build-system).
func TestWorkspaceApp(t *testing.T) {
	wsRoot := fixtures + "/workspace-app"
	lock := loadLock(t, wsRoot)
	local := parseLocalPackages(lock)
	check(t, local, orderedPackages{"my-app": "my-app", "my-lib": "my-lib", "my-core": "my-core", "test-ws-app": "."})

	source := func(name string) map[string]any {
		for _, pkg := range tables(lock["package"]) {
			if pkg["name"] == name {
				return table(pkg, "source")
			}
		}
		t.Fatalf("no package %s", name)
		return nil
	}
	// A package without build-system uses directory (not editable) source in uv.lock.
	_, directory := source("my-app")["directory"]
	_, editable := source("my-app")["editable"]
	check(t, directory && !editable, true)
	_, editable = source("my-lib")["editable"]
	check(t, editable, true)

	check(t, findTransitiveLocalDeps(lock, "my-app", nil), orderedPackages{"my-app": "my-app", "my-lib": "my-lib", "my-core": "my-core"})
	check(t, findTransitiveLocalDeps(lock, "my-lib", nil), orderedPackages{"my-lib": "my-lib", "my-core": "my-core"})

	// my-app has no [build-system] and is flat; my-lib has one and isn't.
	check(t, isFlatPackage(t, wsRoot, local, "my-app"), true)
	check(t, isFlatPackage(t, wsRoot, local, "my-lib"), false)
	check(t, isFlatPackage(t, wsRoot, local, "nonexistent"), false)

	// The original workspace fixture has build-system on all packages.
	ws := fixtures + "/workspace"
	wsLocal := parseLocalPackages(loadLock(t, ws))
	for pkg, path := range wsLocal {
		if path != "." {
			check(t, isFlatPackage(t, ws, wsLocal, pkg), false)
		}
	}
}

// Tests using a workspace where my-lib and my-core use flat layout (no src/).
func TestWorkspaceFlat(t *testing.T) {
	wsRoot := fixtures + "/workspace-flat"
	lock := loadLock(t, wsRoot)
	local := parseLocalPackages(lock)
	check(t, local, orderedPackages{"my-app": "my-app", "my-lib": "my-lib", "my-core": "my-core", "test-ws-flat": "."})
	check(t, findTransitiveLocalDeps(lock, "my-app", nil), orderedPackages{"my-app": "my-app", "my-lib": "my-lib", "my-core": "my-core"})

	for name, path := range local {
		if path == "." {
			continue
		}
		// my-lib and my-core use flat layout (no src/), my-app uses src layout.
		module := strings.ReplaceAll(name, "-", "_")
		srcInit := exists(filepath.Join(wsRoot, path, "src", module, "__init__.py"))
		flatInit := exists(filepath.Join(wsRoot, path, module, "__init__.py"))
		check(t, srcInit, name == "my-app")
		check(t, flatInit, name != "my-app")

		// Every package has [build-system], unlike workspace-app, so none is flat.
		check(t, isFlatPackage(t, wsRoot, local, name), false)
	}
}

func TestBuildUvSyncArgs(t *testing.T) {
	base := []string{"uv", "sync", "--frozen", "--link-mode", "copy"}
	with := func(args ...string) []string { return append(append([]string{}, base...), args...) }
	for _, tc := range []struct {
		options uvSyncOptions
		want    []string
	}{
		{uvSyncOptions{}, base},
		{uvSyncOptions{noEditable: true}, with("--no-editable")},
		{uvSyncOptions{noDefaultGroups: true}, with("--no-default-groups")},
		{uvSyncOptions{allExtras: true}, with("--all-extras")},
		{uvSyncOptions{allGroups: true}, with("--all-groups")},
		{uvSyncOptions{allPackages: true}, with("--all-packages")},
		{uvSyncOptions{extras: []string{"gpu", "viz"}}, with("--extra", "gpu", "--extra", "viz")},
		{uvSyncOptions{groups: []string{"dev", "docs"}}, with("--group", "dev", "--group", "docs")},
		{uvSyncOptions{packages: []string{"my-app"}}, with("--package", "my-app")},
		{uvSyncOptions{packages: []string{"my-app", "my-lib"}}, with("--package", "my-app", "--package", "my-lib")},
		{
			uvSyncOptions{
				packages: []string{"my-app"}, extras: []string{"gpu"}, groups: []string{"dev", "docs"},
				allExtras: true, allGroups: true, allPackages: true,
			},
			with("--all-extras", "--extra", "gpu", "--all-groups", "--group", "dev", "--group", "docs", "--all-packages", "--package", "my-app"),
		},
	} {
		check(t, buildUvSyncArgs(tc.options), tc.want)
	}
}

func TestMatchReachable(t *testing.T) {
	// A package at '.' with workspace path '.' must survive filtering: dirname of
	// 'pyproject.toml' is '' while normpath('.') is '.'.
	packages := orderedPackages{"standalone-app": "."}
	check(t, matchReachable(packages, ".", []string{"pyproject.toml", "src/standalone_app/__init__.py"}), packages)

	packages = orderedPackages{"encode": "../Encode", "l1-lib": "../L1_Lib"}
	check(t, matchReachable(packages, "Icebeam", []string{"Icebeam/pyproject.toml", "Encode/pyproject.toml", "L1_Lib/pyproject.toml"}), packages)

	packages = orderedPackages{"encode": "../Encode", "auto-quest": "../../TypeSafe/projects/AutoQuest"}
	check(t, matchReachable(packages, "Icebeam", []string{"Icebeam/pyproject.toml", "Encode/pyproject.toml"}), orderedPackages{"encode": "../Encode"})

	// The subproject itself (at '.') is kept when the workspace path is a subdirectory.
	packages = orderedPackages{"sub-project": ".", "my-dep": "../my-dep"}
	check(t, matchReachable(packages, "sub-project", []string{"sub-project/pyproject.toml", "my-dep/pyproject.toml"}), packages)

	packages = orderedPackages{"a": ".", "b": "../gone", "c": "sub"}
	check(t, sortedKeys(matchReachable(packages, "ws", []string{"ws/pyproject.toml", "ws/sub/pyproject.toml"})), []string{"a", "c"})
}

func TestModuleName(t *testing.T) {
	check(t, moduleNameOf("my-app"), "my_app")
	check(t, moduleNameOf("my_lib"), "my_lib")
	check(t, moduleNameOf("dagger-io"), "dagger")
}

// PEP 503 name normalization: underscores, mixed case, dots all map to hyphens.
func TestNameNormalization(t *testing.T) {
	check(t, normalizePackageName("frens_in_common"), "frens-in-common")
	check(t, normalizePackageName("my-app"), "my-app")
	check(t, normalizePackageName("My_Package.Name"), "my-package-name")
	check(t, normalizePackageName("a__b--c..d"), "a-b-c-d")

	// An underscored or uppercase project name still resolves against the hyphenated lockfile keys.
	lock := loadLock(t, fixtures+"/workspace")
	want := orderedPackages{"my-app": "my-app", "my-lib": "my-lib", "my-core": "my-core"}
	check(t, findTransitiveLocalDeps(lock, "my_app", nil), want)
	check(t, findTransitiveLocalDeps(lock, "My-App", nil), want)
}

// When the workspace path is a subdirectory, workspace-relative package paths
// (including `..`) must resolve to valid source-root-relative paths for both
// container writes and source reads.
//
// Placing `pyproject.toml`/`uv.lock` at `/work/` regardless of nesting depth
// would resolve `../../Soar` in a lock file to `/work/../../Soar`, escaping the
// filesystem root.
func TestNestedWorkspacePathResolution(t *testing.T) {
	local := parseLocalPackages(loadLock(t, fixtures+"/partial-workspace/sub-project"))

	// `../my-dep` from `sub-project` resolves to `my-dep`.
	check(t, local["my-dep"], "../my-dep")
	check(t, normpath(pjoin("sub-project", "../my-dep")), "my-dep")

	// `../../gone/ext-pkg` resolves outside the source tree, so matchReachable filters it out.
	check(t, local["ext-pkg"], "../../gone/ext-pkg")
	check(t, normpath(pjoin("sub-project", "../../gone/ext-pkg")), "../gone/ext-pkg")

	// Container paths with `..` normalize correctly when the workdir mirrors nesting.
	check(t, normpath(pjoin("/work/sub-project", "../my-dep", "pyproject.toml")), "/work/my-dep/pyproject.toml")
	check(t, normpath(pjoin("/work/sub-project", "../../gone/ext-pkg", "pyproject.toml")), "/gone/ext-pkg/pyproject.toml")

	// When the workspace path is '.', paths pass through unchanged.
	for _, path := range parseLocalPackages(loadLock(t, fixtures+"/workspace")) {
		check(t, normpath(pjoin(".", path)), path)
	}
}

// Like `uv sync`, only the target's own groups are followed, and only the selected ones.
func TestGroupSelection(t *testing.T) {
	lock, err := loadsToml(`
[[package]]
name = "app"
source = { editable = "app" }
dependencies = [{ name = "lib" }]
[package.dev-dependencies]
dev = [{ name = "dev-tool" }]
docs = [{ name = "docs-tool" }]
[[package]]
name = "lib"
source = { editable = "lib" }
[package.dev-dependencies]
dev = [{ name = "lib-test-helper" }]
[[package]]
name = "dev-tool"
source = { editable = "dev-tool" }
[[package]]
name = "docs-tool"
source = { editable = "docs-tool" }
[[package]]
name = "lib-test-helper"
source = { editable = "lib-test-helper" }
`)
	if err != nil {
		t.Fatal(err)
	}
	names := func(groups map[string]bool, project string) []string {
		return sortedKeys(findTransitiveLocalDeps(lock, project, groups))
	}
	check(t, names(nil, "app"), []string{"app", "dev-tool", "docs-tool", "lib"})
	check(t, names(map[string]bool{"docs": true}, "app"), []string{"app", "docs-tool", "lib"})
	check(t, names(map[string]bool{}, "app"), []string{"app", "lib"})
	check(t, names(map[string]bool{"dev": true}, "lib"), []string{"lib", "lib-test-helper"})
}
