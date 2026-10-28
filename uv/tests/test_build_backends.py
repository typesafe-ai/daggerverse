"""Backend settings determine scaffold paths and the files copied for installation."""

import pytest
from uv.workspace.build_backends import BuildBackend, BuildBackendKind
from uv.workspace.build_backends.hatchling import HatchlingSettings
from uv.workspace.build_backends.pdm import PdmSettings
from uv.workspace.build_backends.uv_build import UvBuildSettings


def _system(backend: str, tool: dict | None = None, project: dict | None = None, name: str = "example"):
    metadata = {"build-system": {"build-backend": backend}, "tool": tool or {}, "project": project or {}}
    return BuildBackend.from_pyproject(metadata, name)


def _uv_build(settings: dict | None = None, name: str = "example") -> BuildBackend:
    return _system("uv_build", {"uv": {"build-backend": settings or {}}}, name=name)


def _hatchling(hatch: dict) -> BuildBackend:
    return _system("hatchling.build", {"hatch": hatch})


def _pdm(pdm: dict, name: str = "example") -> BuildBackend:
    return _system("pdm.backend", {"pdm": pdm}, name=name)


@pytest.mark.parametrize(
    ("backend", "kind", "settings"),
    [
        ("uv_build", BuildBackendKind.UV_BUILD, UvBuildSettings),
        ("hatchling.build", BuildBackendKind.HATCHLING, HatchlingSettings),
        ("pdm.backend", BuildBackendKind.PDM, PdmSettings),
        ("setuptools.build_meta", BuildBackendKind.OTHER, type(None)),
        (None, BuildBackendKind.OTHER, type(None)),
    ],
)
def test_kind_discriminates_settings(backend, kind, settings):
    system = BuildBackend.from_pyproject({"build-system": {"build-backend": backend}}, "example")
    assert system.kind is kind
    assert type(system.settings) is settings


def test_other_backend_uses_the_generic_layout():
    system = _system("setuptools.build_meta")
    assert system.module_paths is None
    assert system.source_paths is None
    assert system.build_inputs == []


# ---- [project] inputs ----


@pytest.mark.parametrize(
    ("project", "expected"),
    [
        ({}, []),
        ({"license-files": ["LICEN[CS]E*"], "readme": "docs/PYPI_README.md"}, ["LICEN[CS]E*", "docs/PYPI_README.md"]),
        ({"readme": {"file": "README.rst", "content-type": "text/x-rst"}}, ["README.rst"]),
        ({"readme": {"text": "inline", "content-type": "text/plain"}}, []),
        ({"license": {"file": "COPYING"}}, ["COPYING"]),
        ({"license": "MIT"}, []),
    ],
)
def test_project_inputs(project, expected):
    assert _system("setuptools.build_meta", project=project).build_inputs == expected


def test_build_inputs_deduplicated():
    system = _system(
        "hatchling.build",
        {"hatch": {"version": {"path": "pkg/__init__.py"}}},
        {"license-files": ["LICENSE"], "readme": "pkg/__init__.py"},
    )
    assert system.build_inputs == ["LICENSE", "pkg/__init__.py"]


# ---- uv_build ----


@pytest.mark.parametrize("root", ["", ".", "./"])
def test_uv_build_flat_roots_copy_only_declared_modules(root):
    system = _uv_build({"module-name": ["actual_package", "compat_package"], "module-root": root})
    assert isinstance(system.settings, UvBuildSettings)
    assert system.settings.module_root == ""
    assert system.module_paths == system.source_paths == ["actual_package", "compat_package"]


def test_uv_build_default_module_name_and_source_root():
    system = _uv_build(name="Example.Dist-Name")
    assert system.module_paths == ["src/example_dist_name"]
    assert system.source_paths == ["src"]


def test_uv_build_dotted_module_under_custom_root():
    system = _uv_build({"module-name": "example.api", "module-root": "./python"})
    assert system.module_paths == ["python/example/api"]
    assert system.source_paths == ["python"]


def test_uv_build_data_dirs_are_build_inputs():
    system = _uv_build({"data": {"scripts": "bin", "headers": "./include"}})
    assert system.build_inputs == ["bin", "bin/**", "include", "include/**"]


# ---- hatchling ----


def test_hatchling_without_selection_uses_the_generic_layout():
    system = _hatchling({})
    assert system.module_paths is None
    assert system.source_paths is None
    assert system.build_inputs == []


def test_hatchling_wheel_packages():
    system = _hatchling({"build": {"targets": {"wheel": {"packages": ["src/openai", "./lib/extra"]}}}})
    assert system.module_paths == system.source_paths == ["src/openai", "lib/extra"]


def test_hatchling_wheel_options_override_global_build_options():
    system = _hatchling({"build": {"packages": ["src/a"], "targets": {"wheel": {"packages": ["src/b"]}}}})
    assert system.module_paths == ["src/b"]


def test_hatchling_global_build_packages():
    assert _hatchling({"build": {"packages": ["src/a"]}}).module_paths == ["src/a"]


def test_hatchling_only_include_files_and_directories():
    system = _hatchling({"build": {"targets": {"wheel": {"only-include": ["src/pkg", "single.py", "data.json"]}}}})
    # Modules are stubbed; a non-module file is staged as-is and copied with the sources.
    assert system.module_paths == ["src/pkg", "single.py"]
    assert system.source_paths == ["src/pkg", "single.py", "data.json"]
    assert system.build_inputs == ["data.json"]


def test_hatchling_packages_and_only_include_combine():
    system = _hatchling({"build": {"packages": ["src/pkg"], "only-include": ["extra"]}})
    assert system.module_paths == ["src/pkg", "extra"]


@pytest.mark.parametrize("selection", [{}, {"packages": []}])
def test_hatchling_bypass_selection_or_empty_packages_ship_no_modules(selection):
    system = _hatchling({"build": {"targets": {"wheel": {"bypass-selection": True, **selection}}}})
    assert system.module_paths == system.source_paths == []


def test_hatchling_empty_packages_ship_no_modules():
    assert _hatchling({"build": {"packages": []}}).module_paths == []


def test_hatchling_force_include_and_shared_sources_inside_the_package():
    system = _hatchling(
        {
            "build": {
                "force-include": {"assets": "pkg/assets", "../outside": "pkg/outside", "/abs/x": "pkg/x"},
                "targets": {
                    "wheel": {
                        "force-include": {"CHANGELOG.md": "pkg/CHANGELOG.md"},
                        "shared-data": {"share/man": "share/man"},
                        "shared-scripts": {"bin": "bin"},
                        "extra-metadata": {"NOTICE.txt": "NOTICE.txt"},
                    }
                },
            }
        }
    )
    assert system.build_inputs == [
        "assets",
        "assets/**",
        "CHANGELOG.md",
        "share/man",
        "share/man/**",
        "bin",
        "bin/**",
        "NOTICE.txt",
    ]


def test_hatchling_custom_hook_scripts():
    system = _hatchling(
        {
            "build": {"hooks": {"custom": {}}, "targets": {"wheel": {"hooks": {"custom": {"path": "build/wheel.py"}}}}},
            "metadata": {"hooks": {"custom": {"path": "scripts/hatch_metadata.py"}}},
        }
    )
    assert system.build_inputs == ["hatch_build.py", "build/wheel.py", "scripts/hatch_metadata.py"]


def test_hatchling_non_custom_hooks_add_no_scripts():
    system = _hatchling({"metadata": {"hooks": {"fancy-pypi-readme": {}}}, "build": {"hooks": {"vcs": {}}}})
    assert system.build_inputs == []


def test_hatchling_version_path_is_a_build_input():
    assert _hatchling({"version": {"path": "src/pkg/__about__.py"}}).build_inputs == ["src/pkg/__about__.py"]


# ---- pdm-backend ----


def test_pdm_defaults_use_the_generic_layout_and_stage_the_default_hook():
    system = _pdm({})
    assert system.module_paths is None
    assert system.source_paths is None
    assert system.build_inputs == ["pdm_build.py"]


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ({"source": "file", "path": "fastapi/__init__.py"}, ["pdm_build.py", "fastapi/__init__.py"]),
        ({"source": "scm"}, ["pdm_build.py"]),
    ],
)
def test_pdm_version_file_is_a_build_input(version, expected):
    assert _pdm({"version": version}).build_inputs == expected


@pytest.mark.parametrize(
    ("package_dir", "module_paths", "source_paths"),
    [
        ("src", ["src/my_pkg"], ["src"]),
        ("./lib/", ["lib/my_pkg"], ["lib"]),
        (".", ["my_pkg"], ["my_pkg"]),
    ],
)
def test_pdm_package_dir(package_dir, module_paths, source_paths):
    system = _pdm({"build": {"package-dir": package_dir}}, name="My-Pkg")
    assert system.module_paths == module_paths
    assert system.source_paths == source_paths


def test_pdm_includes_ignore_globs_and_split_modules_from_files():
    system = _pdm({"build": {"includes": ["src/pkg/", "src/pkg/**/*.json", "tool.py", "LICENSE.txt"]}})
    assert system.module_paths == ["src/pkg", "tool.py"]
    assert system.source_paths == ["src/pkg", "tool.py", "LICENSE.txt"]
    assert system.build_inputs == ["pdm_build.py", "LICENSE.txt"]


def test_pdm_custom_hook_path():
    assert _pdm({"build": {"custom-hook": "build/hook.py"}}).build_inputs == ["build/hook.py"]
