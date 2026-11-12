package pyproject

import "testing"

func TestLayout(t *testing.T) {
	m := &Pyproject{
		ProjectItems: []*PyprojectProject{{Path: "app", Include: []string{"app", "app/**"}}},
		WorkspaceItems: []*PyprojectWorkspace{
			{Path: ".", Members: []string{"app"}, ManifestInclude: []string{"uv.lock", "app/pyproject.toml"}},
		},
	}
	got, err := m.Layout()
	if err != nil {
		t.Fatal(err)
	}
	want := `{"projects":[{"path":"app","include":["app","app/**"],"exclude":[],"filesInclude":[],"filesExclude":[]}],` +
		`"workspaces":[{"path":".","members":["app"],"localDependencies":[],"include":[],"exclude":[],` +
		`"manifestInclude":["uv.lock","app/pyproject.toml"],"manifestExclude":[]}]}`
	if got != want {
		t.Errorf("got  %s\nwant %s", got, want)
	}
}
