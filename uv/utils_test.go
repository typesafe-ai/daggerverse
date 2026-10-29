package uv

import (
	"strings"
	"testing"
)

func TestRequirePackageSelection(t *testing.T) {
	check(t, requirePackageSelection(nil, false, "my-app"), nil) // current package -> no error
	check(t, requirePackageSelection([]string{"my-app"}, false, ""), nil)
	check(t, requirePackageSelection(nil, true, ""), nil)
	err := requirePackageSelection(nil, false, "")
	if err == nil || !strings.Contains(err.Error(), "pure workspace root") {
		t.Errorf("unexpected error %v", err)
	}
}

func TestSelectedGroups(t *testing.T) {
	set := func(groups ...string) map[string]bool {
		result := map[string]bool{}
		for _, g := range groups {
			result[g] = true
		}
		return result
	}
	for _, tc := range []struct {
		pyproject                  string
		groups                     []string
		allGroups, noDefaultGroups bool
		want                       map[string]bool
	}{
		{"", nil, false, false, set("dev")},
		{"", []string{"docs"}, false, false, set("dev", "docs")},
		{"", []string{"docs"}, false, true, set("docs")},
		{"", nil, true, true, nil},
		{"[tool.uv]\ndefault-groups = [\"lint\", \"test\"]\n", nil, false, false, set("lint", "test")},
		{"[tool.uv]\ndefault-groups = \"all\"\n", nil, false, false, nil},
		{"[tool.uv]\ndefault-groups = \"all\"\n", []string{"docs"}, false, true, set("docs")},
	} {
		got, err := selectedGroups(tc.pyproject, tc.groups, tc.allGroups, tc.noDefaultGroups)
		check(t, err, nil)
		check(t, got, tc.want)
	}
}

func TestImageRef(t *testing.T) {
	check(t, imageRef("0.9.6"), "ghcr.io/astral-sh/uv:0.9.6")
}

func TestDebianImageRef(t *testing.T) {
	check(t, debianImageRef("0.9.6"), "ghcr.io/astral-sh/uv:0.9.6-debian")
}

func TestParsePyvenvCfg(t *testing.T) {
	parsed := parsePyvenvCfg("home = /root/.local/share/uv/python/cpython-3.13.7-linux-aarch64-gnu/bin\nrelocatable = true\nversion = 3.13.7\n")
	check(t, parsed["home"], "/root/.local/share/uv/python/cpython-3.13.7-linux-aarch64-gnu/bin")
	check(t, parsed["relocatable"], "true")
	check(t, parsed["version"], "3.13.7")
	check(t, parsePyvenvCfg("\n# comment without equals\nkey = value\n"), map[string]string{"key": "value"})
}

func TestParseProjectName(t *testing.T) {
	name, _ := parseProjectName("[project]\nname = \"my-app\"\nversion = \"0\"\n")
	check(t, name, ptr("my-app"))
	name, _ = parseProjectName("[tool.uv.workspace]\nmembers = []\n")
	check(t, name, (*string)(nil))
}

func TestIsExactVersion(t *testing.T) {
	check(t, isExactVersion("0.5.0"), true)
	check(t, isExactVersion("==0.5.0"), true)
	check(t, isExactVersion(">=0.5.0"), false)
	check(t, isExactVersion(">=0.5.0,<0.6.0"), false)
	check(t, isExactVersion("==0.5.*"), false)
}

func TestNormalizeExactVersion(t *testing.T) {
	check(t, normalizeExactVersion("0.5.0"), "0.5.0")
	check(t, normalizeExactVersion("== 0.5.0 "), "0.5.0")
}

func TestParseRequiredVersion(t *testing.T) {
	v, _ := parseRequiredVersionFromPyproject("[tool.uv]\nrequired-version = \">=0.5.0\"\n")
	check(t, v, ptr(">=0.5.0"))
	v, _ = parseRequiredVersionFromPyproject("[project]\nname = \"x\"\n")
	check(t, v, (*string)(nil))
	v, _ = parseRequiredVersionFromUvToml("required-version = \"==0.5.0\"\n")
	check(t, v, ptr("==0.5.0"))
	v, _ = parseRequiredVersionFromUvToml("index-strategy = \"first-index\"\n")
	check(t, v, (*string)(nil))
}

func TestMinimalCompatibleVersion(t *testing.T) {
	for spec, want := range map[string]*string{
		">=0.4.0,<0.6.0": ptr("0.4.0"),
		">=0.6.0":        ptr("0.6.0"),
		"~=0.5":          ptr("0.5.0"),
		"==0.5.*":        ptr("0.5.0"),
		">0.5.0":         ptr("0.5.1"),
		"<1.0.0":         nil,
	} {
		got, err := minimalCompatibleVersion(spec)
		check(t, err, nil)
		check(t, got, want)
	}
}

func TestFormatAuditFailure(t *testing.T) {
	msg := formatAuditFailure(1, "", "found 1 vulnerability in urllib3", "")
	if !strings.Contains(msg, "exit code 1") || !strings.Contains(msg, "found 1 vulnerability in urllib3") {
		t.Error(msg)
	}
	msg = formatAuditFailure(2, "vulnerable: urllib3 2.6.3", "", "")
	if !strings.Contains(msg, "exit code 2") || !strings.Contains(msg, "vulnerable: urllib3 2.6.3") {
		t.Error(msg)
	}
	msg = formatAuditFailure(1, "stdout report", "stderr report", "")
	if !strings.Contains(msg, "stdout report") || !strings.Contains(msg, "stderr report") {
		t.Error(msg)
	}
	check(t, strings.Count(formatAuditFailure(1, "same report", "same report", ""), "same report"), 1)
	if msg := formatAuditFailure(1, "", "  report  \n", ""); !strings.HasSuffix(msg, "report") {
		t.Error(msg)
	}
	check(t, formatAuditFailure(1, "", "", ""), "uv audit failed (exit code 1)")
	check(t, formatAuditFailure(1, "   ", "\n", ""), "uv audit failed (exit code 1)")
	msg = formatAuditFailure(1, "", "found 1 vulnerability", "pkgs/app")
	if !strings.HasPrefix(msg, "uv audit failed for pkgs/app (exit code 1):") || !strings.Contains(msg, "found 1 vulnerability") {
		t.Error(msg)
	}
	check(t, formatAuditFailure(1, "", "", "pkgs/app"), "uv audit failed for pkgs/app (exit code 1)")
}

func parse(t *testing.T, content string, uvToml bool) []*UvIndex {
	t.Helper()
	indices, err := parseIndices(content, uvToml)
	if err != nil {
		t.Fatal(err)
	}
	return indices
}

func TestParseIndices(t *testing.T) {
	result := parse(t, "[[tool.uv.index]]\nname = \"pytorch\"\nurl = \"https://download.pytorch.org/whl/cpu\"\n", false)
	check(t, len(result), 1)
	check(t, result[0].Name, "pytorch")
	check(t, result[0].Url, "https://download.pytorch.org/whl/cpu")
	check(t, result[0].PublishUrl, (*string)(nil))

	result = parse(t, "[[index]]\nname = \"internal\"\nurl = \"https://pypi.internal.dev/simple\"\n", true)
	check(t, len(result), 1)
	check(t, result[0].Name, "internal")
	check(t, result[0].Url, "https://pypi.internal.dev/simple")

	result = parse(t, "[[tool.uv.index]]\nname = \"corp\"\nurl = \"https://corp.dev/simple\"\npublish-url = \"https://corp.dev/upload\"\n", false)
	check(t, result[0].PublishUrl, ptr("https://corp.dev/upload"))

	result = parse(t, "[[tool.uv.index]]\nname = \"zeta\"\nurl = \"https://z.dev\"\n\n[[tool.uv.index]]\nname = \"alpha\"\nurl = \"https://a.dev\"\n", false)
	check(t, indexNames(result), []string{"alpha", "zeta"})

	check(t, parse(t, "[[tool.uv.index]]\nurl = \"https://unnamed.dev\"\n", false), []*UvIndex{})
	check(t, parse(t, "[[tool.uv.index]]\nname = \"no-url\"\n", false), []*UvIndex{})
	check(t, parse(t, "[project]\nname = \"x\"\n", false), []*UvIndex{})

	corp := "[[tool.uv.index]]\nname = \"corp\"\nurl = \"https://corp.dev\"\n"
	check(t, parse(t, corp+"default = true\n", false)[0].Default, true)
	check(t, parse(t, corp+"explicit = true\n", false)[0].Explicit, true)
	check(t, parse(t, corp+"authenticate = \"always\"\n", false)[0].Authenticate, ptr("always"))
	check(t, parse(t, "[[tool.uv.index]]\nname = \"local\"\nurl = \"file:///wheels\"\nformat = \"flat\"\n", false)[0].Format, ptr("flat"))

	plain := parse(t, "[[tool.uv.index]]\nname = \"plain\"\nurl = \"https://plain.dev\"\n", false)[0]
	check(t, plain.Default, false)
	check(t, plain.Explicit, false)
	check(t, plain.Authenticate, (*string)(nil))
	check(t, plain.Format, (*string)(nil))
}

func TestMergeIndices(t *testing.T) {
	index := func(name, url string) *UvIndex { return &UvIndex{Name: name, Url: url} }

	check(t, mergeIndices([]*UvIndex{index("alpha", "https://a.dev")}, nil), []*UvIndex{index("alpha", "https://a.dev")})
	check(t, mergeIndices(nil, []*UvIndex{index("beta", "https://b.dev")}), []*UvIndex{index("beta", "https://b.dev")})

	newer := &UvIndex{Name: "corp", Url: "https://new.dev", PublishUrl: ptr("https://new.dev/upload")}
	result := mergeIndices([]*UvIndex{index("corp", "https://old.dev")}, []*UvIndex{newer})
	check(t, len(result), 1)
	check(t, result[0].Url, "https://new.dev")
	check(t, result[0].PublishUrl, ptr("https://new.dev/upload"))

	result = mergeIndices([]*UvIndex{index("zeta", "https://z.dev")}, []*UvIndex{index("alpha", "https://a.dev")})
	check(t, indexNames(result), []string{"alpha", "zeta"})

	result = mergeIndices(
		[]*UvIndex{index("alpha", "https://a.dev"), index("shared", "https://old.dev")},
		[]*UvIndex{index("beta", "https://b.dev"), index("shared", "https://new.dev")},
	)
	check(t, indexNames(result), []string{"alpha", "beta", "shared"})
	check(t, result[2].Url, "https://new.dev")

	check(t, mergeIndices(nil, nil), []*UvIndex{})

	result = mergeIndices([]*UvIndex{{Name: "corp", Url: "https://corp.dev", Default: true, Authenticate: ptr("always")}}, nil)
	check(t, result[0].Default, true)
	check(t, result[0].Authenticate, ptr("always"))

	result = mergeIndices(
		[]*UvIndex{{Name: "corp", Url: "https://old.dev"}},
		[]*UvIndex{{Name: "corp", Url: "https://new.dev", Default: true, Explicit: true, Authenticate: ptr("always"), Format: ptr("flat")}},
	)
	check(t, result[0].Url, "https://new.dev")
	check(t, result[0].Default, true)
	check(t, result[0].Explicit, true)
	check(t, result[0].Authenticate, ptr("always"))
	check(t, result[0].Format, ptr("flat"))
}

func TestResolveSpecifier(t *testing.T) {
	for spec, want := range map[string]string{
		"0.5.0":          "0.5.0",
		"==0.5.0":        "0.5.0",
		">=0.4.0,<0.6.0": "0.4.0",
		"<1.0.0":         "0.11.19",
	} {
		got, err := resolveSpecifier(spec)
		check(t, err, nil)
		check(t, got, want)
	}
}

func TestPythonRequest(t *testing.T) {
	got, _ := pythonRequest(ptr("3.13.7"), "[project]\nrequires-python = \">=3.12\"\n")
	check(t, got, ptr("3.13.7"))
	got, _ = pythonRequest(nil, "[project]\nrequires-python = \">=3.12\"\n")
	check(t, got, ptr(">=3.12"))
	got, _ = pythonRequest(nil, "[tool.uv.workspace]\n")
	check(t, got, (*string)(nil))
}

// Beyond the ported suite: the PEP 440 subset and posixpath equivalents the Go port adds.

func TestResolveSpecifierEdgeCases(t *testing.T) {
	for spec, want := range map[string]string{
		"~=0.5":            "0.5.0",
		"==0.5.*":          "0.5.0",
		">0.5.0":           "0.5.1",
		">=0.5,!=0.5.0":    defaultVersion,
		">=0.5.0rc1,<0.6":  "0.5.0rc1",
		">=0.5.0, <0.5.5 ": "0.5.0",
	} {
		got, err := resolveSpecifier(spec)
		check(t, err, nil)
		check(t, got, want)
	}
}

func TestPaths(t *testing.T) {
	check(t, isFile("a/b.txt"), true)
	check(t, isFile("a/.hidden"), false)
	check(t, isFile("pkg"), false)
	check(t, dirname("a"), "")
	check(t, dirname("/a"), "/")
	check(t, pjoin("/work", "."), "/work/.")
	check(t, projectOfSrcInit("src/pkg/__init__.py"), ".")
	check(t, projectOfSrcInit("libs/a/src/pkg/__init__.py"), "libs/a")
	check(t, fullMatch("packages/a", "packages/*"), true)
	check(t, fullMatch("packages/a/b", "packages/*"), false)
	check(t, fullMatch("packages/a/b", "packages/**"), true)
}
