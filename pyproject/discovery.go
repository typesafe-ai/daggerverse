// Project and workspace discovery, with no Dagger calls.
//
// Every directory holding a `pyproject.toml` is a project, and every file belongs
// to the deepest project containing it. Every directory holding a workspace
// marker (a lockfile by default) is a workspace: the environment root that
// installs its members and their local dependencies.
//
// A discovery takes what the Dagger layer read from the workspace (project roots,
// manifest text, which markers each workspace root holds) and works out the rest,
// so it can be unit-tested without an engine. Paths are workspace-root-relative,
// `.` for the root.

package pyproject

import (
	"fmt"
	"path"
	"regexp"
	"slices"
	"sort"
	"strings"

	"github.com/go-git/go-git/v5/plumbing/format/gitignore"
	"github.com/pelletier/go-toml/v2"
)

// Never part of any project's input: caches, virtualenvs, VCS metadata and build output.
var sourceExclude = []string{
	"**/__pycache__",
	"**/*.pyc",
	"**/node_modules",
	"**/.venv",
	"**/.tox",
	"**/.nox",
	"**/.git",
	"**/.mypy_cache",
	"**/.pytest_cache",
	"**/.ruff_cache",
	"**/.direnv",
	"**/.devenv",
	"**/dist",
	"**/build",
	"**/*.egg-info",
}

var separators = regexp.MustCompile(`[-_.]+`)

// normalizeName is PEP 503 name normalization: lowercase, collapse [-_.] runs to a single hyphen.
func normalizeName(name string) string {
	return strings.ToLower(separators.ReplaceAllString(name, "-"))
}

// rootRelative returns a workspace-root-relative path for p, which is relative to the workspace cwd.
//
// `Workspace.findRoots` answers relative to the cwd (`.` for the cwd itself,
// `../x` for a sibling); every path here is relative to the workspace root so
// it doesn't depend on where `dagger` runs.
func rootRelative(cwd, p string) string {
	return join(strings.TrimLeft(cwd, "/"), p)
}

// join resolves p against the root-relative directory base, normalized.
func join(base, p string) string {
	if strings.HasPrefix(p, "/") {
		return path.Clean(p)
	}
	return path.Join(base, p)
}

// under reports whether root-relative p is root or below it.
func under(p, root string) bool {
	return root == "." || p == root || strings.HasPrefix(p, root+"/")
}

// escapes reports whether a normalized root-relative path points above the workspace root.
func escapes(p string) bool {
	return p == ".." || strings.HasPrefix(p, "../")
}

// matches reports whether p matches any glob in patterns (`*` within a segment, `**` across segments).
//
// A trailing `/**` also matches the directory itself, so `docs/**` selects
// `docs` and everything below it.
func matches(p string, patterns []string) bool {
	for _, pattern := range patterns {
		pattern = strings.TrimRight(strings.TrimPrefix(pattern, "./"), "/")
		if fullMatch(p, pattern) || (strings.HasSuffix(pattern, "/**") && fullMatch(p, pattern[:len(pattern)-3])) {
			return true
		}
	}
	return false
}

func fullMatch(p, pattern string) bool {
	return matchSegments(strings.Split(p, "/"), strings.Split(pattern, "/"))
}

func matchSegments(segments, pattern []string) bool {
	if len(pattern) == 0 {
		return len(segments) == 0
	}
	if pattern[0] == "**" {
		for i := range len(segments) + 1 {
			if matchSegments(segments[i:], pattern[1:]) {
				return true
			}
		}
		return false
	}
	if len(segments) == 0 {
		return false
	}
	ok, _ := path.Match(pattern[0], segments[0])
	return ok && matchSegments(segments[1:], pattern[1:])
}

var requirementNamePattern = regexp.MustCompile(`^\s*([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)`)

// requirementName returns the normalized project name a PEP 508 requirement string starts with.
func requirementName(requirement string) (string, bool) {
	found := requirementNamePattern.FindStringSubmatch(requirement)
	if found == nil {
		return "", false
	}
	return normalizeName(found[1]), true
}

// table returns data[key] as a table, or nil when it is missing or no table.
func table(data map[string]any, key string) map[string]any {
	t, _ := data[key].(map[string]any)
	return t
}

// stringList returns the strings in a TOML array, skipping anything else.
func stringList(value any) []string {
	items, _ := value.([]any)
	var out []string
	for _, item := range items {
		if s, ok := item.(string); ok {
			out = append(out, s)
		}
	}
	return out
}

// declaredDependencies returns the normalized names of every dependency a `pyproject.toml` declares.
//
// `[project].dependencies`, every `[project.optional-dependencies]` extra,
// every `[dependency-groups]` group (whose `{ include-group = ... }` entries
// name groups, not packages) and the legacy `[tool.uv] dev-dependencies`.
func declaredDependencies(data map[string]any) map[string]bool {
	project := table(data, "project")
	requirements := stringList(project["dependencies"])
	for _, extra := range table(project, "optional-dependencies") {
		requirements = append(requirements, stringList(extra)...)
	}
	for _, group := range table(data, "dependency-groups") {
		requirements = append(requirements, stringList(group)...)
	}
	requirements = append(requirements, stringList(table(table(data, "tool"), "uv")["dev-dependencies"])...)
	names := map[string]bool{}
	for _, r := range requirements {
		if name, ok := requirementName(r); ok {
			names[name] = true
		}
	}
	return names
}

// tree returns include patterns covering p and everything below it.
func tree(p string) []string {
	if p == "." {
		return []string{"**"}
	}
	return []string{p, p + "/**"}
}

// topmost returns the paths in paths that no other path in it contains.
func topmost(paths []string) []string {
	unique := sortedUnique(paths)
	var out []string
	for _, p := range unique {
		if !slices.ContainsFunc(unique, func(q string) bool { return q != p && under(p, q) }) {
			out = append(out, p)
		}
	}
	return out
}

func sortedUnique(items []string) []string {
	out := slices.Clone(items)
	sort.Strings(out)
	return slices.Compact(out)
}

// dedupe drops repeated items, keeping the first of each.
func dedupe(items []string) []string {
	seen := map[string]bool{}
	var out []string
	for _, item := range items {
		if !seen[item] {
			seen[item] = true
			out = append(out, item)
		}
	}
	return out
}

// gitignoreFiles are the `.gitignore` files of a workspace, each applying below its own directory.
//
// `Workspace.findRoots` walks gitignored directories too, on a git-backed
// workspace as much as on a `Directory.asWorkspace()` one, so a gitignored
// copy of the repository (e.g. an agent's worktree) would otherwise turn up
// as a second set of projects.
type gitignoreFiles []gitignoreFile

type gitignoreFile struct {
	directory string
	matcher   gitignore.Matcher
}

// newGitignore takes each root-relative directory to its `.gitignore` text.
func newGitignore(files map[string]string) gitignoreFiles {
	var out gitignoreFiles
	for _, directory := range sortedKeys(files) {
		var patterns []gitignore.Pattern
		for _, line := range strings.Split(files[directory], "\n") {
			line = strings.TrimSuffix(line, "\r")
			if !strings.HasPrefix(line, "#") && strings.TrimSpace(line) != "" {
				patterns = append(patterns, gitignore.ParsePattern(line, nil))
			}
		}
		out = append(out, gitignoreFile{directory, gitignore.NewMatcher(patterns)})
	}
	return out
}

// ignores reports whether a root-relative path is ignored by any `.gitignore` above it.
func (g gitignoreFiles) ignores(p string) bool {
	for _, file := range g {
		if p != file.directory && under(p, file.directory) {
			relative := p
			if file.directory != "." {
				relative = p[len(file.directory)+1:]
			}
			if file.matcher.Match(strings.Split(relative, "/"), false) {
				return true
			}
		}
	}
	return false
}

func sortedKeys[V any](m map[string]V) []string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	return keys
}

// source is one `[tool.uv.sources]` entry, as its declaring manifest resolved it.
type source struct {
	// Root-relative `path` targets (one per marker-split alternative).
	paths []string
	// Whether any alternative is `{ workspace = true }`.
	workspace bool
}

// manifest holds the parts of a `pyproject.toml` that decide what a project needs.
type manifest struct {
	path string
	name *string
	// Normalized names of every declared dependency (see declaredDependencies).
	dependencies map[string]bool
	// Whether Dagger generated this project (`[tool.dagger] generated`).
	generated bool
	// `[tool.uv.sources]`, keyed by normalized package name.
	sources map[string]source
	// `[tool.uv.workspace] members` globs, or nil when this is no uv workspace root.
	members       []string
	memberExclude []string
}

func parseManifest(p, text string) (*manifest, error) {
	var data map[string]any
	if err := toml.Unmarshal([]byte(text), &data); err != nil {
		return nil, fmt.Errorf("%s: %w", join(p, "pyproject.toml"), err)
	}
	tool := table(data, "tool")
	uv := table(tool, "uv")
	sources := map[string]source{}
	for name, value := range table(uv, "sources") {
		// A source is a table, or a list of tables split by environment markers.
		alternatives, ok := value.([]any)
		if !ok {
			alternatives = []any{value}
		}
		var s source
		for _, alt := range alternatives {
			alt, _ := alt.(map[string]any)
			if target, ok := alt["path"].(string); ok {
				s.paths = append(s.paths, join(p, target))
			}
			if alt["workspace"] == true {
				s.workspace = true
			}
		}
		sources[normalizeName(name)] = s
	}
	m := &manifest{
		path:         p,
		dependencies: declaredDependencies(data),
		sources:      sources,
	}
	if name, ok := table(data, "project")["name"].(string); ok {
		m.name = &name
	}
	_, m.generated = table(tool, "dagger")["generated"]
	if workspace := table(uv, "workspace"); workspace != nil {
		m.members = append([]string{}, stringList(workspace["members"])...)
		m.memberExclude = stringList(workspace["exclude"])
	}
	return m, nil
}

type discoveredProject struct {
	path              string
	name              *string
	generated         bool
	workspace         *string
	nested            []string
	localDependencies []string
	include           []string
	exclude           []string
}

type discoveredWorkspace struct {
	path              string
	lockfiles         []string
	members           []string
	localDependencies []string
	include           []string
	exclude           []string
	manifestInclude   []string
}

// discovery holds the projects and workspaces over one snapshot of a Dagger workspace.
//
// Manifests are parsed on first use, so a malformed `pyproject.toml` only
// fails discovery when something actually depends on it.
type discovery struct {
	texts          map[string]string
	projectPaths   []string
	workspacePaths []string
	lockfiles      map[string][]string

	manifests   map[string]*manifest
	members     map[string][]string
	workspaceOf map[string]*string
	closures    map[string][]string
	projects    map[string]*discoveredProject
	workspaces  map[string]*discoveredWorkspace
}

func newDiscovery(manifests map[string]string, lockfiles map[string][]string) *discovery {
	sorted := map[string][]string{}
	for p, names := range lockfiles {
		sorted[p] = slices.Sorted(slices.Values(names))
	}
	return &discovery{
		texts:          manifests,
		projectPaths:   sortedKeys(manifests),
		workspacePaths: sortedKeys(lockfiles),
		lockfiles:      sorted,
		manifests:      map[string]*manifest{},
		members:        map[string][]string{},
		workspaceOf:    map[string]*string{},
		closures:       map[string][]string{},
		projects:       map[string]*discoveredProject{},
		workspaces:     map[string]*discoveredWorkspace{},
	}
}

func (d *discovery) isProject(p string) bool {
	_, ok := d.texts[p]
	return ok
}

func (d *discovery) manifest(p string) (*manifest, error) {
	if m, ok := d.manifests[p]; ok {
		return m, nil
	}
	m, err := parseManifest(p, d.texts[p])
	if err != nil {
		return nil, err
	}
	d.manifests[p] = m
	return m, nil
}

// memberPaths returns the projects a workspace root installs as members, the root project first.
//
// A uv workspace (`[tool.uv.workspace]`) has the root plus every project
// below it matching `members` and not `exclude`, as uv decides. Any other
// workspace has just its root project, or none for a bare lockfile.
func (d *discovery) memberPaths(root string) ([]string, error) {
	if members, ok := d.members[root]; ok {
		return members, nil
	}
	members, err := d.findMembers(root)
	if err != nil {
		return nil, err
	}
	d.members[root] = members
	return members, nil
}

func (d *discovery) findMembers(root string) ([]string, error) {
	if !d.isProject(root) {
		return []string{}, nil
	}
	m, err := d.manifest(root)
	if err != nil {
		return nil, err
	}
	members := []string{root}
	if m.members == nil {
		return members, nil
	}
	for _, p := range d.projectPaths {
		if p == root || !under(p, root) {
			continue
		}
		rel := p
		if root != "." {
			rel = p[len(root)+1:]
		}
		if matches(rel, m.members) && !matches(rel, m.memberExclude) {
			members = append(members, p)
		}
	}
	return members, nil
}

// workspaceRoot returns the deepest workspace that has the project at p as a member.
func (d *discovery) workspaceRoot(p string) (*string, error) {
	if w, ok := d.workspaceOf[p]; ok {
		return w, nil
	}
	var owner *string
	ownerDepth := -1
	for _, w := range d.workspacePaths {
		if !under(p, w) {
			continue
		}
		members, err := d.memberPaths(w)
		if err != nil {
			return nil, err
		}
		if !slices.Contains(members, p) {
			continue
		}
		depth := 0
		if w != "." {
			depth = 1 + strings.Count(w, "/")
		}
		if depth > ownerDepth {
			owner, ownerDepth = &w, depth
		}
	}
	d.workspaceOf[p] = owner
	return owner, nil
}

// directDependencies returns the root-relative paths the project at p depends on through `[tool.uv.sources]`.
//
// As in uv, a source only applies to a package the project declares as a
// dependency; an entry for anything else is ignored. A uv workspace member
// inherits the root's sources, overridden per package by its own. `{ workspace = true }` names a member of the same
// workspace. Paths above the workspace root can't be loaded and are left
// out; paths that don't exist stay in, and simply load nothing.
func (d *discovery) directDependencies(p string) (map[string]bool, error) {
	m, err := d.manifest(p)
	if err != nil {
		return nil, err
	}
	workspace, err := d.workspaceRoot(p)
	if err != nil {
		return nil, err
	}
	sources := map[string]source{}
	members := map[string]string{}
	if workspace != nil {
		root, err := d.manifest(*workspace)
		if err != nil {
			return nil, err
		}
		if *workspace != p && root.members != nil {
			for name, s := range root.sources {
				sources[name] = s
			}
		}
		paths, err := d.memberPaths(*workspace)
		if err != nil {
			return nil, err
		}
		for _, member := range paths {
			mm, err := d.manifest(member)
			if err != nil {
				return nil, err
			}
			if mm.name != nil {
				members[normalizeName(*mm.name)] = member
			}
		}
	}
	for name, s := range m.sources {
		sources[name] = s
	}
	dependencies := map[string]bool{}
	for name, s := range sources {
		if !m.dependencies[name] {
			continue
		}
		for _, target := range s.paths {
			dependencies[target] = true
		}
		if member, ok := members[name]; s.workspace && ok {
			dependencies[member] = true
		}
	}
	for dep := range dependencies {
		if dep == p || escapes(dep) {
			delete(dependencies, dep)
		}
	}
	return dependencies, nil
}

// closure returns every local dependency of the project at p, transitively, without p itself.
func (d *discovery) closure(p string) ([]string, error) {
	if c, ok := d.closures[p]; ok {
		return c, nil
	}
	seen := map[string]bool{}
	pending := []string{p}
	for len(pending) > 0 {
		current := pending[len(pending)-1]
		pending = pending[:len(pending)-1]
		dependencies, err := d.directDependencies(current)
		if err != nil {
			return nil, err
		}
		for dependency := range dependencies {
			if !seen[dependency] && dependency != p {
				seen[dependency] = true
				if d.isProject(dependency) {
					pending = append(pending, dependency)
				}
			}
		}
	}
	c := sortedKeys(seen)
	d.closures[p] = c
	return c, nil
}

// unrelatedNested returns the topmost projects nested in a loaded tree that hold nothing in loaded.
//
// A project's files are its own, so a tree loaded for one project leaves
// out every project nested in it that isn't needed. That keeps a monorepo
// root's source from carrying every project below it, and its digest from
// changing with theirs.
func (d *discovery) unrelatedNested(loaded []string) []string {
	var nested []string
	for _, p := range d.projectPaths {
		inLoaded := slices.ContainsFunc(loaded, func(t string) bool { return under(p, t) })
		holdsLoaded := slices.ContainsFunc(loaded, func(t string) bool { return under(t, p) })
		if inLoaded && !holdsLoaded {
			nested = append(nested, p)
		}
	}
	return topmost(nested)
}

// loads returns include and exclude patterns loading root and needed from the workspace root.
func (d *discovery) loads(root string, needed []string) (include, exclude []string) {
	needed = sortedUnique(needed)
	include = tree(root)
	for _, p := range needed {
		include = append(include, tree(p)...)
	}
	exclude = slices.Clone(sourceExclude)
	for _, p := range d.unrelatedNested(append([]string{root}, needed...)) {
		exclude = append(exclude, tree(p)...)
	}
	return dedupe(include), exclude
}

func (d *discovery) project(p string) (*discoveredProject, error) {
	if project, ok := d.projects[p]; ok {
		return project, nil
	}
	closure, err := d.closure(p)
	if err != nil {
		return nil, err
	}
	m, err := d.manifest(p)
	if err != nil {
		return nil, err
	}
	workspace, err := d.workspaceRoot(p)
	if err != nil {
		return nil, err
	}
	include, exclude := d.loads(p, closure)
	var nested []string
	for _, q := range d.projectPaths {
		if q != p && under(q, p) {
			nested = append(nested, q)
		}
	}
	localDependencies := []string{}
	for _, dep := range closure {
		if d.isProject(dep) {
			localDependencies = append(localDependencies, dep)
		}
	}
	project := &discoveredProject{
		path:              p,
		name:              m.name,
		generated:         m.generated,
		workspace:         workspace,
		nested:            append([]string{}, topmost(nested)...),
		localDependencies: localDependencies,
		include:           include,
		exclude:           exclude,
	}
	d.projects[p] = project
	return project, nil
}

func (d *discovery) workspace(p string) (*discoveredWorkspace, error) {
	if workspace, ok := d.workspaces[p]; ok {
		return workspace, nil
	}
	members, err := d.memberPaths(p)
	if err != nil {
		return nil, err
	}
	dependencySet := map[string]bool{}
	for _, member := range members {
		closure, err := d.closure(member)
		if err != nil {
			return nil, err
		}
		for _, dep := range closure {
			dependencySet[dep] = true
		}
	}
	for _, member := range members {
		delete(dependencySet, member)
	}
	dependencies := sortedKeys(dependencySet)
	include, exclude := d.loads(p, append(slices.Clone(members), dependencies...))
	localDependencies := []string{}
	for _, dep := range dependencies {
		if d.isProject(dep) {
			localDependencies = append(localDependencies, dep)
		}
	}
	var manifestInclude []string
	for _, name := range d.lockfiles[p] {
		manifestInclude = append(manifestInclude, join(p, name))
	}
	manifestInclude = append(manifestInclude, join(p, "uv.toml"))
	for _, project := range append(slices.Clone(members), localDependencies...) {
		manifestInclude = append(manifestInclude, join(project, "pyproject.toml"), join(project, ".python-version"))
	}
	// A path source that isn't a project is an archive (or a directory uv
	// builds with a legacy backend): resolution needs the whole thing.
	for _, dep := range dependencies {
		if !d.isProject(dep) {
			manifestInclude = append(manifestInclude, tree(dep)...)
		}
	}
	workspace := &discoveredWorkspace{
		path:              p,
		lockfiles:         d.lockfiles[p],
		members:           members,
		localDependencies: localDependencies,
		include:           include,
		exclude:           exclude,
		manifestInclude:   dedupe(manifestInclude),
	}
	d.workspaces[p] = workspace
	return workspace, nil
}
