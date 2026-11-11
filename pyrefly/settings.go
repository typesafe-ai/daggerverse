package pyrefly

import (
	"bytes"
	"fmt"

	"github.com/pelletier/go-toml/v2"
)

// The `pyproject.toml` table a workspace overrides the module's settings in.
const settingsTable = "tool.typesafe_daggerverse.pyrefly"

// settings are the module settings a workspace can override.
type settings struct {
	Groups            []string `toml:"groups"`
	Extras            []string `toml:"extras"`
	AllExtras         bool     `toml:"all-extras"`
	NoInstallPackages []string `toml:"no-install-packages"`
	Args              []string `toml:"args"`
}

// override returns the settings with the keys the `pyproject.toml`'s table sets replaced by its values.
//
// A key the table leaves out keeps the module's setting. A key that is no
// setting is an error, so a typo doesn't pass for a setting left at its default.
func (s settings) override(pyproject string) (settings, error) {
	var manifest struct {
		Tool struct {
			TypesafeDaggerverse struct {
				Pyrefly map[string]any `toml:"pyrefly"`
			} `toml:"typesafe_daggerverse"`
		} `toml:"tool"`
	}
	if err := toml.Unmarshal([]byte(pyproject), &manifest); err != nil {
		return s, err
	}
	table, err := toml.Marshal(manifest.Tool.TypesafeDaggerverse.Pyrefly)
	if err != nil {
		return s, err
	}
	if err := toml.NewDecoder(bytes.NewReader(table)).DisallowUnknownFields().Decode(&s); err != nil {
		return s, fmt.Errorf("[%s]: %w", settingsTable, err)
	}
	return s, nil
}
