// Pure helpers for the uv module (no Dagger runtime required).
package uv

import (
	"embed"
	"errors"
	"fmt"
	"maps"
	"path"
	"regexp"
	"slices"
	"strings"

	"github.com/BurntSushi/toml"
)

const uvCacheDir = "/mnt/cache/uv"

var versionSpecifierRe = regexp.MustCompile(`[><=!~]`)

//go:embed images/*/Dockerfile
var images embed.FS

// pinnedImage is the image reference in this package's `images/<name>/Dockerfile`, which Dependabot keeps current.
func pinnedImage(name string) string {
	dockerfile := path.Join("images", name, "Dockerfile")
	content, err := images.ReadFile(dockerfile)
	if err != nil {
		panic(err)
	}
	for line := range strings.SplitSeq(string(content), "\n") {
		if strings.HasPrefix(line, "FROM ") {
			return strings.Fields(line)[1]
		}
	}
	panic(fmt.Sprintf("no FROM line in %s", dockerfile))
}

// The uv image and version used when a workspace doesn't declare
// `required-version`, so audits and builds run on a pinned image.
var defaultImage, defaultVersion = func() (string, string) {
	ref := pinnedImage("uv")
	i := strings.LastIndex(ref, ":")
	return ref[:i], ref[i+1:]
}()

// imageRef is the (distroless) uv image reference for a given tag/version.
//
// `FROM scratch` with only the uv binary. Enough to run `uv audit`, but not
// `uv sync`: with no libc/OS layer, uv can't provision a managed Python.
func imageRef(version string) string {
	return defaultImage + ":" + version
}

// debianImageRef is a Debian-based uv image reference for a given tag/version.
//
// Unlike the distroless `imageRef`, this variant ships a libc/OS layer and
// ca-certificates, so `uv` can download a managed Python at sync time. Used as
// the default build base.
//
// Maps to the `:<version>-debian` tag. `astral-sh/uv` publishes `-debian` for
// both old and current uv releases (the `-bookworm` variant was dropped for
// recent versions).
func debianImageRef(version string) string {
	return defaultImage + ":" + version + "-debian"
}

func isExactVersion(value string) bool {
	stripped := strings.TrimSpace(value)
	if strings.HasPrefix(stripped, "==") {
		return !strings.Contains(stripped, "*")
	}
	return !versionSpecifierRe.MatchString(stripped)
}

func normalizeExactVersion(value string) string {
	if rest, ok := strings.CutPrefix(value, "=="); ok {
		return strings.TrimSpace(rest)
	}
	return strings.TrimSpace(value)
}

// parsePyvenvCfg parses a venv's `pyvenv.cfg` (simple `key = value` lines) into a map.
//
// Used by `newUvVenv` to read the base interpreter (`home`) and the
// `relocatable` flag straight from the venv uv created.
func parsePyvenvCfg(content string) map[string]string {
	result := map[string]string{}
	for _, line := range splitLines(content) {
		key, value, ok := strings.Cut(line, "=")
		if ok {
			result[strings.TrimSpace(key)] = strings.TrimSpace(value)
		}
	}
	return result
}

// splitLines mirrors Python's `str.splitlines()` for `\n`/`\r\n` line endings.
func splitLines(content string) []string {
	lines := strings.Split(strings.ReplaceAll(content, "\r\n", "\n"), "\n")
	if len(lines) > 0 && lines[len(lines)-1] == "" {
		lines = lines[:len(lines)-1]
	}
	return lines
}

// TOML access helpers, mirroring Python's chained `dict.get(key, {})`.

// orderKey holds a table's keys in document order (Go maps have none, Python dicts do).
const orderKey = "\x00order"

func loadsToml(content string) (map[string]any, error) {
	data := map[string]any{}
	md, err := toml.Decode(content, &data)
	if err != nil {
		return nil, err
	}
	for _, key := range md.Keys() {
		parent, last := table(data, key[:len(key)-1]...), key[len(key)-1]
		order, _ := parent[orderKey].([]string)
		if !slices.Contains(order, last) {
			parent[orderKey] = append(order, last)
		}
	}
	return data, nil
}

// keys returns a table's keys in document order.
func keys(data map[string]any) []string {
	order, ok := data[orderKey].([]string)
	if !ok {
		return sortedKeys(data)
	}
	return filter(order, func(key string) bool { _, ok := data[key]; return ok })
}

// table returns the nested table at `keys`, or an empty one when any is missing.
func table(data map[string]any, keys ...string) map[string]any {
	for _, key := range keys {
		next, ok := data[key].(map[string]any)
		if !ok {
			return map[string]any{}
		}
		data = next
	}
	return data
}

// tables returns an array of tables, from either `[[x]]` or an inline array.
func tables(value any) []map[string]any {
	switch v := value.(type) {
	case []map[string]any:
		return v
	case []any:
		result := make([]map[string]any, 0, len(v))
		for _, item := range v {
			if t, ok := item.(map[string]any); ok {
				result = append(result, t)
			}
		}
		return result
	}
	return nil
}

// anyList returns an array's items, whether TOML declared it inline or as `[[x]]`.
func anyList(value any) []any {
	switch v := value.(type) {
	case []any:
		return v
	case []map[string]any:
		result := make([]any, len(v))
		for i, item := range v {
			result[i] = item
		}
		return result
	}
	return nil
}

// stringList returns a string array, or false when `value` is not one.
func stringList(value any) ([]string, bool) {
	switch v := value.(type) {
	case []string:
		return v, true
	case []any:
		result := make([]string, 0, len(v))
		for _, item := range v {
			s, ok := item.(string)
			if !ok {
				return nil, false
			}
			result = append(result, s)
		}
		return result, true
	}
	return nil, false
}

func optionalString(data map[string]any, key string) *string {
	if s, ok := data[key].(string); ok {
		return &s
	}
	return nil
}

func extractIndices(data map[string]any, uvToml bool) []*UvIndex {
	raw := data["index"]
	if !uvToml {
		raw = table(data, "tool", "uv")["index"]
	}
	results := []*UvIndex{}
	for _, idx := range tables(raw) {
		name, _ := idx["name"].(string)
		url, _ := idx["url"].(string)
		if name != "" && url != "" {
			def, _ := idx["default"].(bool)
			explicit, _ := idx["explicit"].(bool)
			results = append(results, &UvIndex{
				Name:         name,
				Url:          url,
				PublishUrl:   optionalString(idx, "publish-url"),
				Default:      def,
				Explicit:     explicit,
				Authenticate: optionalString(idx, "authenticate"),
				Format:       optionalString(idx, "format"),
			})
		}
	}
	slices.SortStableFunc(results, func(a, b *UvIndex) int { return strings.Compare(a.Name, b.Name) })
	return results
}

func parseIndices(content string, uvToml bool) ([]*UvIndex, error) {
	data, err := loadsToml(content)
	if err != nil {
		return nil, err
	}
	return extractIndices(data, uvToml), nil
}

// parseProjectName returns the `[project].name` declared in pyproject.toml, if any.
//
// Used to mimic `uv sync`'s default of operating on the current package when
// no explicit package is requested.
func parseProjectName(content string) (*string, error) {
	data, err := loadsToml(content)
	if err != nil {
		return nil, err
	}
	return optionalString(table(data, "project"), "name"), nil
}

// selectedGroups returns the dependency groups `uv sync` installs, or nil for all of them.
//
// `content` is the workspace root's pyproject.toml, whose `[tool.uv]
// default-groups` (`["dev"]` unless set, or `"all"`) is added unless
// `noDefaultGroups` is set.
func selectedGroups(content string, groups []string, allGroups, noDefaultGroups bool) (map[string]bool, error) {
	if allGroups {
		return nil, nil
	}
	data, err := loadsToml(content)
	if err != nil {
		return nil, err
	}
	raw, ok := table(data, "tool", "uv")["default-groups"]
	if !ok {
		raw = []any{"dev"}
	}
	var defaults []string
	if !noDefaultGroups {
		if raw == "all" {
			return nil, nil
		}
		defaults, _ = stringList(raw)
	}
	result := map[string]bool{}
	for _, group := range slices.Concat(groups, defaults) {
		result[group] = true
	}
	return result, nil
}

// pythonRequest is the Python to provision: the `.python-version` pin, else `[project].requires-python`.
//
// Passed to `uv venv --python` and `uv python install` before the workspace's
// `pyproject.toml` is in the container, so they pick what `uv sync` would.
func pythonRequest(pythonVersion *string, pyproject string) (*string, error) {
	if pythonVersion != nil && *pythonVersion != "" {
		return pythonVersion, nil
	}
	data, err := loadsToml(pyproject)
	if err != nil {
		return nil, err
	}
	return optionalString(table(data, "project"), "requires-python"), nil
}

func parseRequiredVersionFromPyproject(content string) (*string, error) {
	data, err := loadsToml(content)
	if err != nil {
		return nil, err
	}
	return optionalString(table(data, "tool", "uv"), "required-version"), nil
}

func parseRequiredVersionFromUvToml(content string) (*string, error) {
	data, err := loadsToml(content)
	if err != nil {
		return nil, err
	}
	return optionalString(data, "required-version"), nil
}

// padRelease pads a bare `X` or `X.Y` release to a concrete `X.Y.Z` image tag.
//
// Trailing zeros don't change PEP 440 ordering (`0.5` == `0.5.0`), so this
// keeps specifier satisfaction intact while producing a tag that
// exists in the registry (uv publishes three-component tags).
func padRelease(v version) version {
	if v.epoch != 0 || v.pre != nil || v.post != nil || v.dev != nil || v.local != nil {
		return v
	}
	for len(v.release) < 3 {
		v.release = append(slices.Clone(v.release), 0)
	}
	return v
}

// minimalCompatibleVersion is the lowest version satisfying a PEP 440 specifier, computed without PyPI.
//
// Derives candidate lower bounds straight from the specifier's operands
// (`>=`, `>`, `~=`, `==`) and returns the smallest one the whole set
// accepts. Returns nil when the specifier has no lower bound (e.g. only
// `<`/`<=`/`!=` constraints), leaving the caller to pick a default.
func minimalCompatibleVersion(specifier string) (*string, error) {
	spec, err := parseSpecifierSet(specifier)
	if err != nil {
		return nil, err
	}
	var candidates []version
	for _, clause := range spec {
		switch clause.operator {
		case ">=", "~=", "==":
			v, err := parseVersion(strings.TrimSuffix(clause.version, ".*"))
			if err != nil {
				return nil, err
			}
			candidates = append(candidates, padRelease(v))
		case ">":
			v, err := parseVersion(clause.version)
			if err != nil {
				return nil, err
			}
			release := slices.Clone(padRelease(v).release)
			release[len(release)-1]++
			candidates = append(candidates, version{release: release})
		}
	}
	var feasible []version
	for _, c := range candidates {
		if spec.contains(c) {
			feasible = append(feasible, c)
		}
	}
	if len(feasible) == 0 {
		return nil, nil
	}
	smallest := slices.MinFunc(feasible, compareVersions).String()
	return &smallest, nil
}

// formatAuditFailure is the human-readable message for a failed `uv audit` exec.
//
// `uv audit` writes its vulnerability report to stdout/stderr, but the
// ExecError it raises stringifies to only a terse "exit code N" message.
// Folding the captured output into the message keeps the report in the
// trace/span error instead of leaving it solely in Dagger's stderr logs. Both
// streams are included (deduplicated) since uv's findings may land on either
// depending on version.
//
// `workspace` (a source-relative path) is named in the summary when given,
// so an aggregated/standalone error identifies which workspace failed.
func formatAuditFailure(exitCode int, stdout, stderr, workspace string) string {
	var seen []string
	for _, stream := range []string{stdout, stderr} {
		text := strings.TrimSpace(stream)
		if text != "" && !slices.Contains(seen, text) {
			seen = append(seen, text)
		}
	}
	target := ""
	if workspace != "" {
		target = " for " + workspace
	}
	summary := fmt.Sprintf("uv audit failed%s (exit code %d)", target, exitCode)
	if len(seen) == 0 {
		return summary
	}
	return summary + ":\n\n" + strings.Join(seen, "\n")
}

// resolveSpecifier is the concrete image tag for a configured version value (exact pin or range).
//
// Exact pins are used verbatim; ranges resolve to their minimal compatible
// version. Falls back to the default tag when a range has no lower bound.
func resolveSpecifier(value string) (string, error) {
	if isExactVersion(value) {
		return normalizeExactVersion(value), nil
	}
	minimal, err := minimalCompatibleVersion(value)
	if err != nil {
		return "", err
	}
	if minimal == nil || *minimal == "" {
		return defaultVersion, nil
	}
	return *minimal, nil
}

var packageNameSeparators = regexp.MustCompile(`[-_.]+`)

// normalizePackageName is PEP 503 name normalization: lowercase, collapse [-_.] runs to a single hyphen.
func normalizePackageName(name string) string {
	return strings.ToLower(packageNameSeparators.ReplaceAllString(name, "-"))
}

// requirePackageSelection ensures there is a package to install, else errors.
//
// `uv sync` defaults to the current package; we mirror that via
// `defaultPackage` (the workspace root's `[project].name`). A pure
// workspace root has no `[project].name`, so with no explicit selection
// there is nothing to install. Surface that as an error rather than silently
// installing every member.
func requirePackageSelection(packages []string, allPackages bool, defaultPackage string) error {
	if len(packages) > 0 || allPackages || defaultPackage != "" {
		return nil
	}
	return errors.New("No package to install: this workspace's pyproject.toml declares no " +
		"[project].name (a pure workspace root), so there is no current package " +
		"to default to. Pass `package` to select one or more members, or set " +
		"`all_packages` to install every workspace member.")
}

// orderedPackages maps package names to local paths; iterate with `sortedKeys` for deterministic order.
type orderedPackages map[string]string

func sortedKeys[V any](m map[string]V) []string {
	return slices.Sorted(maps.Keys(m))
}

// parseLocalPackages returns {package_name: local_path} for editable, directory, and virtual packages.
//
// Virtual members still need their metadata staged for workspace resolution.
func parseLocalPackages(lockData map[string]any) orderedPackages {
	result := orderedPackages{}
	for _, pkg := range tables(lockData["package"]) {
		source := table(pkg, "source")
		name, _ := pkg["name"].(string)
		for _, kind := range []string{"editable", "directory", "virtual"} {
			if value, ok := source[kind]; ok {
				result[name], _ = value.(string)
				break
			}
		}
	}
	return result
}

// findTransitiveLocalDeps finds all local packages that `project` transitively depends on (including itself).
//
// The *project* name is PEP 503-normalised so callers can pass the raw
// `[project].name` from `pyproject.toml` (which may use underscores).
//
// Like `uv sync`, dependency groups apply only to `project` itself, never to
// its dependencies: `groups` selects which of its groups are followed (nil
// follows all of them).
//
// Virtual roots and members participate in traversal just like installable
// packages, including dependencies reached through a virtual member.
func findTransitiveLocalDeps(lockData map[string]any, project string, groups map[string]bool) orderedPackages {
	locals := parseLocalPackages(lockData)
	project = normalizePackageName(project)

	depGraph := map[string][]string{}
	for _, pkg := range tables(lockData["package"]) {
		name, _ := pkg["name"].(string)
		deps := map[string]bool{}
		for _, d := range tables(pkg["dependencies"]) {
			dep, _ := d["name"].(string)
			deps[dep] = true
		}
		if name == project {
			devDependencies := table(pkg, "dev-dependencies")
			for _, group := range keys(devDependencies) {
				if groups == nil || groups[group] {
					for _, d := range tables(devDependencies[group]) {
						dep, _ := d["name"].(string)
						deps[dep] = true
					}
				}
			}
		}
		depGraph[name] = sortedKeys(deps)
	}

	needed := orderedPackages{}
	// Seed traversal from `project` even when it isn't local so we still walk
	// its dependencies. Only local packages are recorded in `needed` and traversed
	// (a third-party dep's transitive deps are remote, not workspace-local).
	visited := map[string]bool{project: true}
	queue := []string{project}
	if p, ok := locals[project]; ok {
		needed[project] = p
	}
	for len(queue) > 0 {
		current := queue[0]
		queue = queue[1:]
		for _, dep := range depGraph[current] {
			if !visited[dep] {
				visited[dep] = true
				if p, ok := locals[dep]; ok {
					needed[dep] = p
					queue = append(queue, dep)
				}
			}
		}
	}
	return needed
}

type uvSyncOptions struct {
	packages        []string
	extras          []string
	groups          []string
	allExtras       bool
	allGroups       bool
	allPackages     bool
	noEditable      bool
	noDefaultGroups bool
}

func buildUvSyncArgs(o uvSyncOptions) []string {
	args := []string{"uv", "sync", "--frozen", "--link-mode", "copy"}
	if o.noEditable {
		args = append(args, "--no-editable")
	}
	if o.noDefaultGroups {
		args = append(args, "--no-default-groups")
	}
	if o.allExtras {
		args = append(args, "--all-extras")
	}
	for _, extra := range o.extras {
		args = append(args, "--extra", extra)
	}
	if o.allGroups {
		args = append(args, "--all-groups")
	}
	for _, group := range o.groups {
		args = append(args, "--group", group)
	}
	if o.allPackages {
		args = append(args, "--all-packages")
	}
	for _, pkg := range o.packages {
		args = append(args, "--package", pkg)
	}
	return args
}

// posixpath equivalents: Python's `posixpath.join` and `dirname` don't clean
// their result, and the container paths they build must match exactly.

func pjoin(base string, parts ...string) string {
	result := base
	for _, part := range parts {
		switch {
		case strings.HasPrefix(part, "/"):
			result = part
		case result == "" || strings.HasSuffix(result, "/"):
			result += part
		default:
			result += "/" + part
		}
	}
	return result
}

func dirname(p string) string {
	head := p[:strings.LastIndex(p, "/")+1]
	if trimmed := strings.TrimRight(head, "/"); trimmed != "" {
		return trimmed
	}
	return head
}

func basename(p string) string {
	return p[strings.LastIndex(p, "/")+1:]
}

// normpath is `posixpath.normpath`; it differs from `path.Clean` only for `//`-rooted paths.
func normpath(p string) string {
	return path.Clean(p)
}
