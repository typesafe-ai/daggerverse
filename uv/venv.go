package uv

import (
	"context"
	"errors"
	"fmt"
	"strings"

	"dagger.io/dagger/core"

	"dagger/uv/internal/dagger"
)

// A relocatable uv virtual environment bundled with the uv-managed Python it needs.
//
// A venv never contains the standard library: `.venv/bin/python` resolves to
// a base interpreter whose `lib/` provides it. This pairs the venv with that
// interpreter so the two can be dropped into a fresh container (one without any
// Python of its own) and run without uv.
//
// Only valid for a venv created with `build(venv_relocatable=True)` against a
// *uv-managed* (standalone) Python: uv's standalone Pythons are themselves
// relocatable, and a relocatable venv keeps working when its directory is moved.
// The venv may be mounted at any path, but the Python must be remounted at
// `python_path` (the absolute location its `pyvenv.cfg` records).
//
// The bundled Python is the python-build-standalone variant uv selected for the
// base image's platform/libc (set by `base_container` at build time), so the
// target container must match it: a Debian (glibc) base exports to glibc targets,
// an Alpine (musl) base to musl/alpine targets.
type UvVenv struct {
	// The relocatable `.venv` directory.
	Venv *dagger.Directory
	// uv's managed-Python store (the interpreter the venv links against, plus uv's version symlinks).
	Python *dagger.Directory
	// Absolute path the Python store must be mounted at for the venv to resolve its interpreter.
	PythonPath string
}

// newUvVenv builds a `UvVenv` from a container holding a populated venv at `venvPath`.
//
// Reads the venv's `pyvenv.cfg` to locate uv's managed-Python store and
// exports the whole store alongside the venv. Errors unless the venv is
// relocatable and its Python is uv-managed (the only combination this can
// faithfully export).
func newUvVenv(ctx context.Context, container *dagger.Container, venvPath string) (*UvVenv, error) {
	content, err := container.File(pjoin(venvPath, "pyvenv.cfg")).Contents(ctx)
	if err != nil {
		return nil, err
	}
	cfg := parsePyvenvCfg(content)
	if strings.ToLower(cfg["relocatable"]) != "true" {
		return nil, errors.New("UvVenv requires a relocatable venv; create it with build(venv_relocatable=True).")
	}
	// `home` is the interpreter's bin/ dir: <store>/<impl-dir>/bin, where <store> is uv's
	// managed-Python store (default ~/.local/share/uv/python, or $UV_PYTHON_INSTALL_DIR) and
	// <impl-dir> is a python-build-standalone install, e.g. cpython-3.14-<plat>. uv names that
	// dir with the MINOR version and symlinks it to the real cpython-<patch>-<plat> dir; the
	// venv references the minor path by absolute symlink. Export the STORE (the parent of the
	// install dir, derived structurally with no hardcoded location, so $UV_PYTHON_INSTALL_DIR
	// works too) so the version symlink AND the real interpreter travel together and resolve
	// when remounted, and so we never extract a symlinked path directly (which
	// `Container.directory()` has mishandled across engine versions).
	home := strings.TrimRight(cfg["home"], "/")
	installDir := dirname(home) // <store>/<impl-dir>
	pythonPath := dirname(installDir)
	implDir := basename(installDir)
	if !strings.HasPrefix(implDir, "cpython-") && !strings.HasPrefix(implDir, "pypy-") && !strings.HasPrefix(implDir, "graalpy-") {
		return nil, fmt.Errorf("UvVenv only supports uv-managed (standalone) Pythons; the venv's interpreter dir "+
			"%s isn't a python-build-standalone install. Install one with "+
			"with_python_install().", pyRepr(installDir))
	}
	return &UvVenv{
		Venv:       container.Directory(venvPath),
		Python:     container.Directory(pythonPath),
		PythonPath: pythonPath,
	}, nil
}

// pyRepr quotes a string like Python's `repr`, for messages that mirror Python's.
func pyRepr(s string) string {
	if strings.Contains(s, "'") && !strings.Contains(s, `"`) {
		return `"` + s + `"`
	}
	return "'" + strings.ReplaceAll(s, "'", `\'`) + "'"
}

// Mount the venv (at `path`) and its Python (at `python_path`) into `container`.
//
// With `set_env_vars`, exports `VIRTUAL_ENV` and prepends the venv's `bin/`
// to `PATH` so `python`/console scripts resolve without activation;
// otherwise the venv is just mounted (run it via `<path>/bin/python`).
func (v *UvVenv) Into(
	ctx context.Context,
	// Container to add the venv and its Python to.
	container *dagger.Container,
	// Where to mount the venv; relative paths resolve against the container's workdir. Defaults to `.venv`.
	// +default=".venv"
	path string,
	// Also set the standard activation env vars (`VIRTUAL_ENV` and a `PATH` with the venv's `bin/` first).
	// +default=false
	setEnvVars bool,
) (*dagger.Container, error) {
	workdir, err := container.Workdir(ctx)
	if err != nil {
		return nil, err
	}
	venvPath := path
	if !strings.HasPrefix(path, "/") {
		venvPath = pjoin(workdir, path)
	}
	ctr := container.WithDirectory(v.PythonPath, v.Python).WithDirectory(venvPath, v.Venv)
	if setEnvVars {
		ctr = ctr.WithEnvVariable("VIRTUAL_ENV", venvPath).
			WithEnvVariable("PATH", venvPath+"/bin:${PATH}", core.ContainerWithEnvVariableOpts{Expand: true})
	}
	return ctr, nil
}
