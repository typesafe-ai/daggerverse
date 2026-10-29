"""Backend settings determine scaffold paths and the files copied for installation."""

import asyncio
import subprocess

import pytest
from uv.workspace.build_backends import PackageBackend, maturin
from uv.workspace.build_backends.flit import FlitSettings
from uv.workspace.build_backends.handler import BuildBackendLayout, MaturinHandler
from uv.workspace.build_backends.hatchling import HatchlingSettings
from uv.workspace.build_backends.maturin import MaturinSettings, target_prune_script
from uv.workspace.build_backends.pdm import PdmSettings
from uv.workspace.build_backends.poetry import PoetrySettings
from uv.workspace.build_backends.uv_build import UvBuildSettings
from uv.workspace.plan import handlers_by_backend


def _system(backend: str, tool: dict | None = None, project: dict | None = None, name: str = "example"):
    metadata = {"build-system": {"build-backend": backend}, "tool": tool or {}, "project": project or {}}
    return PackageBackend.from_pyproject(metadata, name)


def _uv_build(settings: dict | None = None, name: str = "example") -> PackageBackend:
    return _system("uv_build", {"uv": {"build-backend": settings or {}}}, name=name)


def _hatchling(hatch: dict) -> PackageBackend:
    return _system("hatchling.build", {"hatch": hatch})


def _pdm(pdm: dict, name: str = "example") -> PackageBackend:
    return _system("pdm.backend", {"pdm": pdm}, name=name)


@pytest.mark.parametrize(
    ("backend", "settings"),
    [
        ("uv_build", UvBuildSettings),
        ("hatchling.build", HatchlingSettings),
        ("pdm.backend", PdmSettings),
        ("flit_core.buildapi", FlitSettings),
        ("poetry.core.masonry.api", PoetrySettings),
        ("maturin", MaturinSettings),
        ("setuptools.build_meta", type(None)),
        (None, type(None)),
    ],
)
def test_registry_selects_settings(backend, settings):
    system = PackageBackend.from_pyproject({"build-system": {"build-backend": backend}}, "example")
    assert system.name == backend
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


def test_uv_build_stubs_package_keeps_suffix():
    system = _uv_build(name="example-stubs")
    assert system.module_paths == ["src/example-stubs"]


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


def test_pdm_package_dir_stubs_package_keeps_suffix():
    system = _pdm({"build": {"package-dir": "src"}}, name="my-pkg-stubs")
    assert system.module_paths == ["src/my_pkg-stubs"]


def test_pdm_includes_ignore_globs_and_split_modules_from_files():
    system = _pdm({"build": {"includes": ["src/pkg/", "src/pkg/**/*.json", "tool.py", "LICENSE.txt"]}})
    assert system.module_paths == ["src/pkg", "tool.py"]
    assert system.source_paths == ["src/pkg", "tool.py", "LICENSE.txt"]
    assert system.build_inputs == ["pdm_build.py", "LICENSE.txt"]


def test_pdm_custom_hook_path():
    assert _pdm({"build": {"custom-hook": "build/hook.py"}}).build_inputs == ["build/hook.py"]


# ---- flit_core ----


def _flit(flit: dict, project: dict | None = None, name: str = "example-dist") -> PackageBackend:
    return _system("flit_core.buildapi", {"flit": flit}, project, name=name)


def test_flit_default_module_name_uses_the_generic_layout():
    backend = _flit({})
    assert backend.module_name == "example_dist"
    assert backend.module_paths is None
    assert backend.source_paths is None
    assert backend.build_inputs == []


def test_flit_dotted_module_name():
    assert _flit({"module": {"name": "airflow.providers.smtp"}}).module_name == "airflow.providers.smtp"


@pytest.mark.parametrize("dynamic", [["version"], ["description"], ["version", "description"]])
def test_flit_dynamic_metadata_stages_the_module(dynamic):
    backend = _flit({"module": {"name": "ns.pkg"}}, {"dynamic": dynamic})
    assert backend.build_inputs == ["ns/pkg/__init__.py", "src/ns/pkg/__init__.py"]


def test_flit_static_metadata_stages_nothing():
    assert _flit({}, {"version": "1.0", "description": "x", "dynamic": ["readme"]}).build_inputs == []


def test_flit_external_data():
    assert _flit({"external-data": {"directory": "data"}}).build_inputs == ["data", "data/**"]


@pytest.mark.parametrize(
    "backend", ["uv_build", "hatchling.build", "pdm.backend", "poetry.core.masonry.api", "setuptools.build_meta"]
)
def test_only_flit_declares_a_module_name(backend):
    assert _system(backend).module_name is None


# ---- poetry-core ----


def _poetry(poetry: dict) -> PackageBackend:
    return _system("poetry.core.masonry.api", {"poetry": poetry})


def test_poetry_defaults_use_the_generic_layout():
    backend = _poetry({})
    assert backend.module_paths is None
    assert backend.source_paths is None
    assert backend.build_inputs == []


def test_poetry_packages_join_from_and_skip_sdist_only_entries():
    backend = _poetry(
        {
            "packages": [
                {"include": "cleo", "from": "src"},
                {"include": "extra/**/*.py"},
                {"include": "docs_only", "format": "sdist"},
                {"include": "both", "format": ["sdist", "wheel"]},
                {"include": "single.py"},
            ]
        }
    )
    assert backend.module_paths == backend.source_paths == ["src/cleo", "extra", "both", "single.py"]


def test_poetry_empty_packages_ship_no_modules():
    assert _poetry({"packages": []}).module_paths == []


def test_poetry_wheel_includes_readme_and_build_script():
    backend = _poetry(
        {
            "readme": ["README.md", "CHANGELOG.md"],
            "include": ["pkg/py.typed", "data", "*.cfg", {"path": "tests", "format": "sdist"}],
            "build": {"script": "build.py"},
        }
    )
    assert backend.build_inputs == ["pkg/py.typed", "data", "data/**", "*.cfg", "README.md", "CHANGELOG.md", "build.py"]


def test_poetry_legacy_build_string():
    assert _poetry({"build": "build.py"}).build_inputs == ["build.py"]


# ---- handlers ----


class _SetuptoolsHandler:
    async def build_backend(self) -> str:
        return "setuptools.build_meta"

    async def module_name(self, pyproject: str, name: str) -> str | None:
        return None

    async def module_paths(self, pyproject: str, name: str) -> list[str] | None:
        return [f"lib/{name}"]

    async def source_paths(self, pyproject: str, name: str) -> list[str] | None:
        return ["lib"]

    async def build_inputs(self, pyproject: str, name: str) -> list[str]:
        return ["VERSION"]


def test_handler_layout_resolves_every_answer():
    layout = asyncio.run(BuildBackendLayout.resolve(_SetuptoolsHandler(), "", "plugged"))
    assert layout.module_name is None
    assert layout.module_paths == ["lib/plugged"]
    assert layout.source_paths == ["lib"]
    assert layout.build_inputs == ["VERSION"]


@pytest.mark.parametrize("backend", ["setuptools.build_meta", "hatchling.build"])
def test_handler_layout_replaces_builtin_settings(backend):
    layout = BuildBackendLayout(module_paths=["lib/plugged"], source_paths=["lib"], build_inputs=["VERSION"])
    metadata = {"build-system": {"build-backend": backend}, "project": {"readme": "README.md"}}
    system = PackageBackend.from_pyproject(metadata, "plugged", layout)
    assert system.settings is layout
    assert system.module_paths == ["lib/plugged"]
    assert system.source_paths == ["lib"]
    assert system.build_inputs == ["README.md", "VERSION"]


# ---- maturin ----


def _maturin(maturin: dict, project: dict | None = None, name: str = "example-dist") -> PackageBackend:
    return _system("maturin", {"maturin": maturin}, project, name=name)


def test_maturin_pure_rust_stages_rust_and_stubs_only():
    backend = _maturin({"module-name": "jiter"}, name="jiter")
    assert backend.module_paths == []
    assert backend.source_paths == []
    assert {"Cargo.toml", "Cargo.lock", "src/**", "build.rs"} <= set(backend.build_inputs)
    assert not any(path.startswith("/") for path in backend.build_inputs)
    assert {"jiter.pyi", "py.typed"} <= set(backend.build_inputs)


def test_maturin_default_module_name_is_pure_rust():
    assert _maturin({}).module_paths == []


def test_maturin_dotted_module_is_a_mixed_package_at_the_root():
    backend = _maturin({"module-name": "watchfiles._rust_notify"})
    assert backend.module_paths == backend.source_paths == ["watchfiles"]
    assert "watchfiles._rust_notify.pyi" not in backend.build_inputs


def test_maturin_python_source():
    backend = _maturin({"module-name": "pkg._native", "python-source": "python"})
    assert backend.module_paths == ["python/pkg"]
    assert backend.source_paths == ["python"]


def test_maturin_cargo_metadata_files_and_wheel_includes():
    backend = _maturin(
        {"include": ["data/schema.json", {"path": "sdist-only", "format": "sdist"}, {"path": "assets"}]},
        project={"dynamic": ["version", "readme"]},
    )
    inputs = backend.build_inputs
    assert "README*" in inputs
    assert "LICENSE*" not in inputs
    assert "data/schema.json" in inputs
    assert {"assets", "assets/**"} <= set(inputs)
    assert not any(path.startswith("sdist-only") for path in inputs)


@pytest.mark.parametrize(
    ("max_target_size", "cleared"),
    [(0, True), (1, False)],
)
def test_maturin_target_prune_clears_only_past_the_limit(tmp_path, monkeypatch, max_target_size, cleared):
    target = tmp_path / "target"
    (target / "release").mkdir(parents=True)
    (target / "release" / "libcore.rlib").write_bytes(b"x" * 1024)
    monkeypatch.setattr(maturin, "_CARGO_TARGET_DIR", str(target))
    subprocess.run(["sh", "-c", target_prune_script(max_target_size)], check=True)
    assert target.is_dir()
    assert (not any(target.iterdir())) == cleared


def test_maturin_crate_inputs_follow_the_manifest_path():
    backend = _maturin({"module-name": "pkg._native", "manifest-path": "rust/Cargo.toml"})
    assert {"rust/Cargo.toml", "rust/Cargo.lock", "rust/src/**", "rust/build.rs"} <= set(backend.build_inputs)


# ---- handler selection ----


def test_handlers_by_backend_indexes_each_backend():
    assert handlers_by_backend(["maturin", "hatchling.build"]) == {"maturin": 0, "hatchling.build": 1}
    assert handlers_by_backend([]) == {}


def test_handlers_by_backend_rejects_two_handlers_for_one_backend():
    with pytest.raises(ValueError, match="more than one build backend handler for maturin"):
        handlers_by_backend(["maturin", "hatchling.build", "maturin"])


def test_builtin_handler_adds_package_config_to_the_builtin_answers():
    pyproject = '[build-system]\nbuild-backend = "maturin"\n[tool.maturin]\nmodule-name = "jiter"\n'
    handler = MaturinHandler().with_package_config(name="Jiter", extra_build_inputs=["/Cargo.lock"])
    assert asyncio.run(handler.build_backend()) == "maturin"
    assert asyncio.run(handler.module_paths(pyproject, "jiter")) == []
    jiter = asyncio.run(handler.build_inputs(pyproject, "jiter"))
    assert "Cargo.toml" in jiter
    assert jiter[-1] == "/Cargo.lock"
    assert "/Cargo.lock" not in asyncio.run(handler.build_inputs(pyproject, "other"))


def test_builtin_handler_package_configs_accumulate():
    handler = (
        MaturinHandler()
        .with_package_config(name="a", extra_build_inputs=["a.txt"])
        .with_package_config(name="b", extra_build_inputs=["b.txt"])
    )
    assert handler.package_inputs == {"a": ["a.txt"], "b": ["b.txt"]}
