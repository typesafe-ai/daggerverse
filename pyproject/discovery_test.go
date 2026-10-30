package pyproject

import (
	"fmt"
	"slices"
	"strings"
	"testing"
)

func quoted(items []string) string {
	q := make([]string, len(items))
	for i, item := range items {
		q[i] = fmt.Sprintf("%q", item)
	}
	return "[" + strings.Join(q, ", ") + "]"
}

func project(name, extra string, deps ...string) string {
	return fmt.Sprintf("[project]\nname = %q\nversion = \"0\"\ndependencies = %s\n", name, quoted(deps)) + extra
}

func uvWorkspace(name string, members, exclude []string, extra string) string {
	text := project(name, "") + "\n[tool.uv.workspace]\nmembers = " + quoted(members) + "\n"
	if len(exclude) > 0 {
		text += "exclude = " + quoted(exclude) + "\n"
	}
	return text + extra
}

func equal(t *testing.T, got, want []string) {
	t.Helper()
	if !slices.Equal(got, want) {
		t.Errorf("got %q, want %q", got, want)
	}
}

func must[T any](v T, err error) T {
	if err != nil {
		panic(err)
	}
	return v
}

func workspaceOf(t *testing.T, found *discovery, p string) string {
	t.Helper()
	w := must(found.project(p)).workspace
	if w == nil {
		return "<nil>"
	}
	return *w
}

func TestRootRelative(t *testing.T) {
	for _, c := range [][3]string{{"/", ".", "."}, {"/a/b", "../c", "a/c"}, {"/a", ".", "a"}} {
		if got := rootRelative(c[0], c[1]); got != c[2] {
			t.Errorf("rootRelative(%q, %q) = %q, want %q", c[0], c[1], got, c[2])
		}
	}
}

func TestMatches(t *testing.T) {
	for _, c := range []struct {
		path     string
		patterns []string
		want     bool
	}{
		{"uv/tests/_packages/clean", []string{"**/tests/_packages/**"}, true},
		{"uv/tests/_packages", []string{"**/tests/_packages/**"}, true},
		{"uv/tests", []string{"**/tests/_packages/**"}, false},
		{"packages/a", []string{"packages/*"}, true},
		{"packages/a/b", []string{"packages/*"}, false},
		{"packages/a", []string{"./packages/*/"}, true},
	} {
		if got := matches(c.path, c.patterns); got != c.want {
			t.Errorf("matches(%q, %q) = %v", c.path, c.patterns, got)
		}
	}
}

func TestTopmost(t *testing.T) {
	equal(t, topmost([]string{"a/b", "a", "c/d", "c/d/e", "a"}), []string{"a", "c/d"})
}

func TestGitignore(t *testing.T) {
	ignored := newGitignore(map[string]string{".": ".claude/worktrees/\n"})
	if !ignored.ignores(".claude/worktrees/wt1/pyproject.toml") || ignored.ignores("pyproject.toml") {
		t.Error("directory pattern")
	}
	ignored = newGitignore(map[string]string{"sub": "vendored/\n"})
	if !ignored.ignores("sub/vendored/pyproject.toml") || ignored.ignores("vendored/pyproject.toml") {
		t.Error("nested file")
	}
	ignored = newGitignore(map[string]string{".": "*.lock\n!uv.lock\n"})
	if !ignored.ignores("x/poetry.lock") || ignored.ignores("x/uv.lock") {
		t.Error("negation")
	}
	if newGitignore(nil).ignores("a/pyproject.toml") {
		t.Error("no files")
	}
}

func TestManifest(t *testing.T) {
	m := must(parseManifest("apps/web", project("web", "\n[tool.uv.sources]\nMy_Lib = { path = \"../../libs/my-lib\" }\ncore = { workspace = true }\n")))
	equal(t, m.sources["my-lib"].paths, []string{"libs/my-lib"})
	if !m.sources["core"].workspace {
		t.Error("workspace source")
	}

	m = must(parseManifest(".", project("x", "\n[tool.uv.sources]\ntorch = [{ path = \"wheels/a.whl\", marker = \"sys_platform == 'linux'\" }, { index = \"pytorch\" }]\n")))
	equal(t, m.sources["torch"].paths, []string{"wheels/a.whl"})
	if m.sources["torch"].workspace {
		t.Error("marker split")
	}

	m = must(parseManifest(".", project("x", "\n[tool.uv]\nsources.lib = { path = \"lib\" }\n")))
	equal(t, m.sources["lib"].paths, []string{"lib"})

	m = must(parseManifest(".", project("x",
		"\n[project.optional-dependencies]\nviz = [\"Tabulate>=0.9; python_version>'3'\"]\n"+
			"\n[dependency-groups]\ndev = [\"pytest\", { include-group = \"lint\" }]\nlint = [\"ruff[all]\"]\n"+
			"\n[tool.uv]\ndev-dependencies = [\"mypy==1.0\"]\n",
		"My_Lib @ file:///x")))
	equal(t, sortedKeys(m.dependencies), []string{"my-lib", "mypy", "pytest", "ruff", "tabulate"})

	if !must(parseManifest(".", project("x", "\n[tool.dagger]\ngenerated = \"core\"\n"))).generated ||
		must(parseManifest(".", project("x", "\n[tool.dagger]\nother = 1\n"))).generated ||
		must(parseManifest(".", project("x", ""))).generated {
		t.Error("generated")
	}

	if _, err := parseManifest("a", "[project\n"); err == nil || !strings.HasPrefix(err.Error(), "a/pyproject.toml: ") {
		t.Errorf("invalid toml: %v", err)
	}
}

func TestStandaloneProject(t *testing.T) {
	found := newDiscovery(map[string]string{"app": project("app", "")}, map[string][]string{"app": {"uv.lock"}})
	app := must(found.project("app"))
	if *app.workspace != "app" {
		t.Error(*app.workspace)
	}
	equal(t, app.localDependencies, []string{})
	equal(t, app.include, []string{"app", "app/**"})
	equal(t, app.exclude, sourceExclude)
	ws := must(found.workspace("app"))
	equal(t, ws.members, []string{"app"})
	equal(t, ws.manifestInclude, []string{"app/uv.lock", "app/uv.toml", "app/pyproject.toml", "app/.python-version"})
}

func TestUvWorkspaceMembers(t *testing.T) {
	found := newDiscovery(map[string]string{
		"ws":                   uvWorkspace("root", []string{"packages/*", "tools/cli"}, []string{"packages/skip"}, ""),
		"ws/packages/a":        project("a", ""),
		"ws/packages/skip":     project("skip", ""),
		"ws/packages/a/nested": project("nested", ""),
		"ws/tools/cli":         project("cli", ""),
		"ws/tools/other":       project("other", ""),
	}, map[string][]string{"ws": {"uv.lock"}})
	ws := must(found.workspace("ws"))
	equal(t, ws.members, []string{"ws", "ws/packages/a", "ws/tools/cli"})
	if workspaceOf(t, found, "ws/packages/a") != "ws" || workspaceOf(t, found, "ws/packages/skip") != "<nil>" || workspaceOf(t, found, "ws/tools/other") != "<nil>" {
		t.Error("workspace of")
	}
	equal(t, ws.exclude[len(sourceExclude):], []string{
		"ws/packages/a/nested", "ws/packages/a/nested/**",
		"ws/packages/skip", "ws/packages/skip/**",
		"ws/tools/other", "ws/tools/other/**",
	})
}

func TestWorkspaceWithoutUvWorkspaceTable(t *testing.T) {
	found := newDiscovery(map[string]string{".": project("root", ""), "sub": project("sub", "")}, map[string][]string{".": {"pylock.toml"}})
	ws := must(found.workspace("."))
	equal(t, ws.members, []string{"."})
	equal(t, ws.lockfiles, []string{"pylock.toml"})
	if ws.manifestInclude[0] != "pylock.toml" || workspaceOf(t, found, "sub") != "<nil>" {
		t.Error(ws.manifestInclude)
	}
}

func TestBareLockfile(t *testing.T) {
	found := newDiscovery(map[string]string{}, map[string][]string{"locks": {"pylock.toml"}})
	ws := must(found.workspace("locks"))
	equal(t, ws.members, []string{})
	equal(t, ws.include, []string{"locks", "locks/**"})
}

func TestPathDependencyOutsideWorkspaceRoot(t *testing.T) {
	found := newDiscovery(map[string]string{
		"nested/app":    project("app", "\n[tool.uv.sources]\nlib = { path = \"../my-lib\", editable = true }\n", "lib"),
		"nested/my-lib": project("lib", ""),
	}, map[string][]string{"nested/app": {"uv.lock"}})
	app := must(found.project("nested/app"))
	equal(t, app.localDependencies, []string{"nested/my-lib"})
	equal(t, app.include, []string{"nested/app", "nested/app/**", "nested/my-lib", "nested/my-lib/**"})
	ws := must(found.workspace("nested/app"))
	equal(t, ws.localDependencies, []string{"nested/my-lib"})
	if !slices.Contains(ws.manifestInclude, "nested/my-lib/pyproject.toml") || workspaceOf(t, found, "nested/my-lib") != "<nil>" {
		t.Error(ws.manifestInclude)
	}
}

func TestSourcesForUndeclaredPackagesAreIgnored(t *testing.T) {
	found := newDiscovery(map[string]string{
		".":      uvWorkspace("root", []string{"libs/*"}, nil, "\n[tool.uv.sources]\na = { workspace = true }\nb = { workspace = true }\n"),
		"libs/a": project("a", "", "b"),
		"libs/b": project("b", ""),
		"tool":   project("tool", "\n[tool.uv.sources]\na = { path = \"../libs/a\" }\n", "a"),
	}, map[string][]string{".": {"uv.lock"}, "tool": {"uv.lock"}})
	equal(t, must(found.project("libs/a")).localDependencies, []string{"libs/b"})
	equal(t, must(found.project("libs/b")).localDependencies, []string{})
	equal(t, must(found.project("tool")).localDependencies, []string{"libs/a", "libs/b"})
}

func TestTransitiveAndCyclicDependencies(t *testing.T) {
	found := newDiscovery(map[string]string{
		"app": project("app", "\n[tool.uv.sources]\nb = { path = \"../b\" }\n", "b"),
		"b":   project("b", "\n[tool.uv.sources]\nc = { path = \"../c\" }\n", "c"),
		"c":   project("c", ""),
	}, map[string][]string{"app": {"uv.lock"}})
	equal(t, must(found.project("app")).localDependencies, []string{"b", "c"})

	found = newDiscovery(map[string]string{
		"a": project("a", "\n[tool.uv.sources]\nb = { path = \"../b\" }\n", "b"),
		"b": project("b", "\n[tool.uv.sources]\na = { path = \"../a\" }\n", "a"),
	}, nil)
	equal(t, must(found.project("a")).localDependencies, []string{"b"})
}

func TestMissingAndEscapingSources(t *testing.T) {
	found := newDiscovery(map[string]string{
		"ws/sub": project("sub", "\n[tool.uv.sources]\n"+
			"gone = { path = \"../gone/pkg\" }\n"+
			"outside = { path = \"../../../outside\" }\n"+
			"wheel = { path = \"../wheels/w.whl\" }\n", "gone", "outside", "wheel"),
	}, map[string][]string{"ws/sub": {"uv.lock"}})
	sub := must(found.project("ws/sub"))
	equal(t, sub.localDependencies, []string{})
	equal(t, sub.include, []string{"ws/sub", "ws/sub/**", "ws/gone/pkg", "ws/gone/pkg/**", "ws/wheels/w.whl", "ws/wheels/w.whl/**"})
	if !slices.Contains(must(found.workspace("ws/sub")).manifestInclude, "ws/wheels/w.whl") {
		t.Error("wheel manifest")
	}
}

func TestWorkspaceSourcesResolveByNormalizedName(t *testing.T) {
	found := newDiscovery(map[string]string{
		".":           uvWorkspace("root", []string{"libs/*", "apps/*"}, nil, ""),
		"apps/web":    project("web", "\n[tool.uv.sources]\nmy-lib = { workspace = true }\n", "my-lib"),
		"libs/my_lib": project("My.Lib", ""),
		"libs/unused": project("unused", ""),
	}, map[string][]string{".": {"uv.lock"}})
	web := must(found.project("apps/web"))
	equal(t, web.localDependencies, []string{"libs/my_lib"})
	equal(t, web.include, []string{"apps/web", "apps/web/**", "libs/my_lib", "libs/my_lib/**"})
}

func TestMemberSources(t *testing.T) {
	found := newDiscovery(map[string]string{
		".":             uvWorkspace("root", []string{"apps/*"}, nil, "\n[tool.uv.sources]\nshared = { path = \"vendor/shared\" }\n"),
		"apps/web":      project("web", "", "shared"),
		"vendor/shared": project("shared", ""),
	}, map[string][]string{".": {"uv.lock"}})
	equal(t, must(found.project("apps/web")).localDependencies, []string{"vendor/shared"})

	found = newDiscovery(map[string]string{
		".":        uvWorkspace("root", []string{"apps/*"}, nil, "\n[tool.uv.sources]\nshared = { path = \"vendor/a\" }\n"),
		"apps/web": project("web", "\n[tool.uv.sources]\nshared = { path = \"../../vendor/b\" }\n", "shared"),
		"vendor/a": project("a", ""),
		"vendor/b": project("b", ""),
	}, map[string][]string{".": {"uv.lock"}})
	equal(t, must(found.project("apps/web")).localDependencies, []string{"vendor/b"})
}

func TestRootProjectLeavesOutUnrelatedNested(t *testing.T) {
	found := newDiscovery(map[string]string{
		".":                 project("root", "\n[tool.uv.sources]\nlib = { path = \"lib\" }\n", "lib"),
		"lib":               project("lib", ""),
		"lib/tests/fixture": project("fixture", ""),
		"tools/a":           project("a", ""),
		"tools/a/deep":      project("deep", ""),
	}, map[string][]string{".": {"uv.lock"}})
	root := must(found.project("."))
	equal(t, root.nested, []string{"lib", "tools/a"})
	equal(t, root.include, []string{"**", "lib", "lib/**"})
	equal(t, root.exclude[len(sourceExclude):], []string{"lib/tests/fixture", "lib/tests/fixture/**", "tools/a", "tools/a/**"})
}

func TestNestedWorkspacesAndMarkers(t *testing.T) {
	found := newDiscovery(map[string]string{
		".":         uvWorkspace("outer", []string{"**"}, nil, ""),
		"inner":     uvWorkspace("inner", []string{"pkg"}, nil, ""),
		"inner/pkg": project("pkg", ""),
	}, map[string][]string{".": {"uv.lock"}, "inner": {"uv.lock"}})
	if workspaceOf(t, found, "inner/pkg") != "inner" || workspaceOf(t, found, ".") != "." {
		t.Error("deepest workspace")
	}

	found = newDiscovery(map[string]string{"a": project("a", "")}, map[string][]string{"a": {"uv.lock", "pylock.toml"}})
	equal(t, must(found.workspace("a")).lockfiles, []string{"pylock.toml", "uv.lock"})
}

func TestInvalidManifestOnlyFailsWhenUsed(t *testing.T) {
	found := newDiscovery(map[string]string{"good": project("good", ""), "bad": "[project\n"}, map[string][]string{"good": {"uv.lock"}})
	if *must(found.project("good")).name != "good" {
		t.Error("good")
	}
	if _, err := found.project("bad"); err == nil || !strings.Contains(err.Error(), "bad/pyproject.toml") {
		t.Errorf("bad: %v", err)
	}
}
