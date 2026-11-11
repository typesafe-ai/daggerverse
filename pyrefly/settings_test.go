package pyrefly

import (
	"reflect"
	"strings"
	"testing"
)

var moduleSettings = settings{
	Groups:    []string{"typing"},
	AllExtras: true,
}

func TestOverrideKeepsModuleSettingsWithoutTable(t *testing.T) {
	for _, pyproject := range []string{"", "[project]\nname = \"x\"\n", "[tool.typesafe_daggerverse.deptry]\nargs = [\"-v\"]\n"} {
		got, err := moduleSettings.override(pyproject)
		if err != nil {
			t.Fatal(err)
		}
		if !reflect.DeepEqual(got, moduleSettings) {
			t.Errorf("override(%q) = %+v, want the module settings", pyproject, got)
		}
	}
}

func TestOverrideReplacesOnlyTheKeysSet(t *testing.T) {
	got, err := moduleSettings.override(`
[tool.typesafe_daggerverse.pyrefly]
all-extras = false
extras = ["cpu"]
no-install-packages = ["kernels"]
`)
	if err != nil {
		t.Fatal(err)
	}
	want := settings{
		Groups:            []string{"typing"},
		Extras:            []string{"cpu"},
		NoInstallPackages: []string{"kernels"},
	}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("got %+v, want %+v", got, want)
	}
}

func TestOverrideRejectsUnknownKeys(t *testing.T) {
	_, err := moduleSettings.override("[tool.typesafe_daggerverse.pyrefly]\nallExtras = false\n")
	if err == nil || !strings.Contains(err.Error(), settingsTable) {
		t.Errorf("expected an error naming the table, got %v", err)
	}
}
