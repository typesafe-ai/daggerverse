package uv

// The subset of PEP 440 (as implemented by Python's `packaging`) that
// resolving a `required-version` range needs: parsing, ordering and
// specifier sets.

import (
	"cmp"
	"fmt"
	"regexp"
	"slices"
	"strconv"
	"strings"
)

var versionRe = regexp.MustCompile(`(?i)^\s*v?` +
	`(?:(?P<epoch>[0-9]+)!)?` +
	`(?P<release>[0-9]+(?:\.[0-9]+)*)` +
	`(?P<pre>[-_\.]?(?P<pre_l>alpha|a|beta|b|preview|pre|c|rc)[-_\.]?(?P<pre_n>[0-9]+)?)?` +
	`(?P<post>(?:-(?P<post_n1>[0-9]+))|(?:[-_\.]?(?P<post_l>post|rev|r)[-_\.]?(?P<post_n2>[0-9]+)?))?` +
	`(?P<dev>[-_\.]?(?P<dev_l>dev)[-_\.]?(?P<dev_n>[0-9]+)?)?` +
	`(?:\+(?P<local>[a-z0-9]+(?:[-_\.][a-z0-9]+)*))?` +
	`\s*$`)

type preRelease struct {
	letter string
	number int
}

type version struct {
	epoch   int
	release []int
	pre     *preRelease
	post    *int
	dev     *int
	local   []string
}

func parseVersion(s string) (version, error) {
	m := versionRe.FindStringSubmatch(s)
	if m == nil {
		return version{}, fmt.Errorf("Invalid version: %q", s)
	}
	group := func(name string) string { return m[versionRe.SubexpIndex(name)] }
	number := func(value string) int {
		n, _ := strconv.Atoi(value)
		return n
	}
	var v version
	v.epoch = number(group("epoch"))
	for part := range strings.SplitSeq(group("release"), ".") {
		v.release = append(v.release, number(part))
	}
	if letter := strings.ToLower(group("pre_l")); letter != "" {
		switch letter {
		case "alpha":
			letter = "a"
		case "beta":
			letter = "b"
		case "c", "pre", "preview":
			letter = "rc"
		}
		v.pre = &preRelease{letter, number(group("pre_n"))}
	}
	if group("post") != "" {
		n := number(group("post_n1") + group("post_n2"))
		v.post = &n
	}
	if group("dev") != "" {
		n := number(group("dev_n"))
		v.dev = &n
	}
	if local := group("local"); local != "" {
		for _, part := range regexp.MustCompile(`[-_.]`).Split(strings.ToLower(local), -1) {
			if n, err := strconv.Atoi(part); err == nil {
				part = strconv.Itoa(n)
			}
			v.local = append(v.local, part)
		}
	}
	return v, nil
}

func (v version) String() string {
	var b strings.Builder
	if v.epoch != 0 {
		fmt.Fprintf(&b, "%d!", v.epoch)
	}
	for i, n := range v.release {
		if i > 0 {
			b.WriteByte('.')
		}
		b.WriteString(strconv.Itoa(n))
	}
	if v.pre != nil {
		fmt.Fprintf(&b, "%s%d", v.pre.letter, v.pre.number)
	}
	if v.post != nil {
		fmt.Fprintf(&b, ".post%d", *v.post)
	}
	if v.dev != nil {
		fmt.Fprintf(&b, ".dev%d", *v.dev)
	}
	if v.local != nil {
		b.WriteString("+" + strings.Join(v.local, "."))
	}
	return b.String()
}

func (v version) isPrerelease() bool  { return v.dev != nil || v.pre != nil }
func (v version) isPostrelease() bool { return v.post != nil }

func (v version) public() version {
	v.local = nil
	return v
}

func (v version) baseVersion() version {
	return version{epoch: v.epoch, release: v.release}
}

// compareVersions orders versions like `packaging`'s comparison key.
func compareVersions(a, b version) int {
	if c := cmp.Compare(a.epoch, b.epoch); c != 0 {
		return c
	}
	trim := func(release []int) []int {
		end := len(release)
		for end > 0 && release[end-1] == 0 {
			end--
		}
		return release[:end]
	}
	if c := slices.Compare(trim(a.release), trim(b.release)); c != 0 {
		return c
	}
	// Pre-release: a dev-only release sorts before any pre-release, a final after.
	preRank := func(v version) int {
		switch {
		case v.pre == nil && v.post == nil && v.dev != nil:
			return -1
		case v.pre == nil:
			return 1
		}
		return 0
	}
	if c := cmp.Compare(preRank(a), preRank(b)); c != 0 {
		return c
	}
	if a.pre != nil && b.pre != nil {
		if c := cmp.Or(strings.Compare(a.pre.letter, b.pre.letter), cmp.Compare(a.pre.number, b.pre.number)); c != 0 {
			return c
		}
	}
	// Post-release: none sorts first.
	if c := compareOptional(a.post, b.post, -1); c != 0 {
		return c
	}
	// Development release: none sorts last.
	if c := compareOptional(a.dev, b.dev, 1); c != 0 {
		return c
	}
	return compareLocal(a.local, b.local)
}

// compareOptional compares optional numbers, ranking a missing one at `missing` (-1 first, 1 last).
func compareOptional(a, b *int, missing int) int {
	switch {
	case a == nil && b == nil:
		return 0
	case a == nil:
		return missing
	case b == nil:
		return -missing
	}
	return cmp.Compare(*a, *b)
}

// compareLocal orders local segments: none first, numbers above strings.
func compareLocal(a, b []string) int {
	switch {
	case a == nil && b == nil:
		return 0
	case a == nil:
		return -1
	case b == nil:
		return 1
	}
	for i := range min(len(a), len(b)) {
		an, aErr := strconv.Atoi(a[i])
		bn, bErr := strconv.Atoi(b[i])
		var c int
		switch {
		case aErr == nil && bErr == nil:
			c = cmp.Compare(an, bn)
		case aErr == nil:
			c = 1
		case bErr == nil:
			c = -1
		default:
			c = strings.Compare(a[i], b[i])
		}
		if c != 0 {
			return c
		}
	}
	return cmp.Compare(len(a), len(b))
}

type specifier struct {
	operator string
	version  string
}

type specifierSet []specifier

var specifierOperators = []string{"===", "~=", "==", "!=", "<=", ">=", "<", ">"}

func parseSpecifierSet(s string) (specifierSet, error) {
	var set specifierSet
	for raw := range strings.SplitSeq(s, ",") {
		raw = strings.TrimSpace(raw)
		if raw == "" {
			continue
		}
		spec, err := parseSpecifier(raw)
		if err != nil {
			return nil, err
		}
		set = append(set, spec)
	}
	return set, nil
}

func parseSpecifier(raw string) (specifier, error) {
	for _, op := range specifierOperators {
		if rest, ok := strings.CutPrefix(raw, op); ok {
			spec := specifier{operator: op, version: strings.TrimSpace(rest)}
			if op == "===" {
				return spec, nil
			}
			v := spec.version
			if op == "==" || op == "!=" {
				v = strings.TrimSuffix(v, ".*")
			}
			if _, err := parseVersion(v); err != nil || strings.ContainsAny(v, "*") {
				return specifier{}, fmt.Errorf("Invalid specifier: %q", raw)
			}
			return spec, nil
		}
	}
	return specifier{}, fmt.Errorf("Invalid specifier: %q", raw)
}

// prereleases reports whether the specifier explicitly admits pre-releases.
func (s specifier) prereleases() bool {
	switch s.operator {
	case "==", ">=", "<=", "~=", "===", ">", "<":
		v, err := parseVersion(strings.TrimSuffix(s.version, ".*"))
		return err == nil && v.isPrerelease()
	}
	return false
}

func (set specifierSet) contains(item version) bool {
	prereleases := slices.ContainsFunc(set, specifier.prereleases)
	if !prereleases && item.isPrerelease() {
		return false
	}
	for _, s := range set {
		if !s.contains(item) {
			return false
		}
	}
	return true
}

func (s specifier) contains(prospective version) bool {
	if s.operator == "===" {
		return strings.EqualFold(prospective.String(), s.version)
	}
	if prefix, ok := strings.CutSuffix(s.version, ".*"); ok {
		matches := prefixMatch(prospective, prefix)
		return matches == (s.operator == "==")
	}
	spec, _ := parseVersion(s.version)
	switch s.operator {
	case "==":
		return equalVersion(prospective, spec)
	case "!=":
		return !equalVersion(prospective, spec)
	case "~=":
		prefix := spec.baseVersion()
		prefix.release = prefix.release[:len(prefix.release)-1]
		return compareVersions(prospective.public(), spec) >= 0 && prefixMatch(prospective, prefix.String())
	case "<=":
		return compareVersions(prospective.public(), spec) <= 0
	case ">=":
		return compareVersions(prospective.public(), spec) >= 0
	case "<":
		if compareVersions(prospective, spec) >= 0 {
			return false
		}
		return spec.isPrerelease() || !prospective.isPrerelease() ||
			compareVersions(prospective.baseVersion(), spec.baseVersion()) != 0
	case ">":
		if compareVersions(prospective, spec) <= 0 {
			return false
		}
		sameBase := compareVersions(prospective.baseVersion(), spec.baseVersion()) == 0
		if !spec.isPostrelease() && prospective.isPostrelease() && sameBase {
			return false
		}
		return prospective.local == nil || !sameBase
	}
	return false
}

func equalVersion(prospective, spec version) bool {
	if spec.local == nil {
		prospective = prospective.public()
	}
	return compareVersions(prospective, spec) == 0
}

// prefixMatch is `==prefix.*`: same epoch and release prefix, the prospective release zero-padded.
func prefixMatch(prospective version, prefix string) bool {
	spec, err := parseVersion(prefix)
	if err != nil || spec.epoch != prospective.epoch {
		return false
	}
	release := prospective.release
	for len(release) < len(spec.release) {
		release = append(slices.Clone(release), 0)
	}
	return slices.Equal(release[:len(spec.release)], spec.release)
}
