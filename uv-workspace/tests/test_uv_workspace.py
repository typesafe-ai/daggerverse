"""Tests for uv.lock parsing and transitive dependency resolution."""

import tomllib
from collections import OrderedDict
from pathlib import Path

from uv_workspace._utils import (
    _normalize,
    build_uv_sync_args,
    find_transitive_local_deps,
    parse_local_packages,
)
from uv_workspace.sync_plan import _match_reachable, _module_name

FIXTURES = Path(__file__).parent / "_packages"


def _load_lock(path: Path) -> dict:
    return tomllib.loads((path / "uv.lock").read_text())


def _is_flat_package(ws_root: Path, local_packages: dict[str, str], pkg: str) -> bool:
    """Replicate the flat-package detection from UvWorkspace.build()."""
    if pkg not in local_packages:
        return False
    toml = tomllib.loads((ws_root / local_packages[pkg] / "pyproject.toml").read_text())
    return "build-system" not in toml


class TestWorkspace:
    """Tests using a workspace with my-app -> my-lib -> my-core."""

    lock_data = _load_lock(FIXTURES / "workspace")

    def test_parse_local_packages(self):
        result = parse_local_packages(self.lock_data)
        assert result == {
            "my-app": "my-app",
            "my-lib": "my-lib",
            "my-core": "my-core",
        }

    def test_find_transitive_from_app(self):
        result = find_transitive_local_deps(self.lock_data, "my-app")
        assert result == {
            "my-app": "my-app",
            "my-lib": "my-lib",
            "my-core": "my-core",
        }

    def test_find_transitive_from_lib(self):
        result = find_transitive_local_deps(self.lock_data, "my-lib")
        assert result == {
            "my-lib": "my-lib",
            "my-core": "my-core",
        }

    def test_find_transitive_from_leaf(self):
        result = find_transitive_local_deps(self.lock_data, "my-core")
        assert result == {"my-core": "my-core"}

    def test_skips_virtual_root(self):
        """The workspace root (source = virtual) should not appear in local packages."""
        result = parse_local_packages(self.lock_data)
        assert "test-ws" not in result

    def test_parse_returns_ordered_dict(self):
        result = parse_local_packages(self.lock_data)
        assert isinstance(result, OrderedDict)

    def test_parse_sorted_order(self):
        result = parse_local_packages(self.lock_data)
        assert list(result.keys()) == ["my-app", "my-core", "my-lib"]

    def test_transitive_returns_ordered_dict(self):
        result = find_transitive_local_deps(self.lock_data, "my-app")
        assert isinstance(result, OrderedDict)

    def test_transitive_sorted_order(self):
        result = find_transitive_local_deps(self.lock_data, "my-app")
        assert list(result.keys()) == ["my-app", "my-core", "my-lib"]

    def test_transitive_from_lib_sorted_order(self):
        result = find_transitive_local_deps(self.lock_data, "my-lib")
        assert list(result.keys()) == ["my-core", "my-lib"]


class TestPartialWorkspace:
    """Tests using a workspace where some local deps don't exist in the source tree."""

    lock_data = _load_lock(FIXTURES / "partial-workspace" / "sub-project")

    def test_parse_local_packages_includes_missing(self):
        """parse_local_packages returns ALL local packages from the lock, including missing ones."""
        result = parse_local_packages(self.lock_data)
        assert result == {
            "ext-pkg": "../../gone/ext-pkg",
            "my-dep": "../my-dep",
            "sub-project": ".",
        }

    def test_transitive_includes_missing(self):
        result = find_transitive_local_deps(self.lock_data, "sub-project")
        assert "ext-pkg" in result
        assert "my-dep" in result

    def test_transitive_from_my_dep(self):
        result = find_transitive_local_deps(self.lock_data, "my-dep")
        assert result == {"my-dep": "../my-dep"}


class TestStandalone:
    """Tests using a standalone single-package project."""

    lock_data = _load_lock(FIXTURES / "standalone-app")

    def test_parse_local_packages(self):
        result = parse_local_packages(self.lock_data)
        assert result == {"standalone-app": "."}

    def test_find_transitive(self):
        result = find_transitive_local_deps(self.lock_data, "standalone-app")
        assert result == {"standalone-app": "."}

    def test_find_transitive_unknown_package(self):
        result = find_transitive_local_deps(self.lock_data, "nonexistent")
        assert result == {}


class TestWorkspaceApp:
    """Tests using a workspace where my-app is a flat app (no build-system)."""

    ws_root = FIXTURES / "workspace-app"
    lock_data = _load_lock(ws_root)

    def test_parse_local_packages(self):
        result = parse_local_packages(self.lock_data)
        assert result == {
            "my-app": "my-app",
            "my-lib": "my-lib",
            "my-core": "my-core",
        }

    def test_directory_source_detected(self):
        """A package without build-system uses directory (not editable) source in uv.lock."""
        app_pkg = next(p for p in self.lock_data["package"] if p["name"] == "my-app")
        assert "directory" in app_pkg["source"]
        assert "editable" not in app_pkg["source"]

    def test_lib_still_editable(self):
        lib_pkg = next(p for p in self.lock_data["package"] if p["name"] == "my-lib")
        assert "editable" in lib_pkg["source"]

    def test_find_transitive_from_app(self):
        result = find_transitive_local_deps(self.lock_data, "my-app")
        assert result == {
            "my-app": "my-app",
            "my-lib": "my-lib",
            "my-core": "my-core",
        }

    def test_find_transitive_from_lib(self):
        result = find_transitive_local_deps(self.lock_data, "my-lib")
        assert result == {
            "my-lib": "my-lib",
            "my-core": "my-core",
        }

    def test_flat_package_detection_app(self):
        """my-app has no [build-system] and should be detected as flat."""
        local = parse_local_packages(self.lock_data)
        assert _is_flat_package(self.ws_root, local, "my-app") is True

    def test_flat_package_detection_lib(self):
        """my-lib has a [build-system] and should NOT be detected as flat."""
        local = parse_local_packages(self.lock_data)
        assert _is_flat_package(self.ws_root, local, "my-lib") is False

    def test_flat_package_detection_unknown(self):
        local = parse_local_packages(self.lock_data)
        assert _is_flat_package(self.ws_root, local, "nonexistent") is False

    def test_original_workspace_not_flat(self):
        """The original workspace fixture has build-system on all packages."""
        ws = FIXTURES / "workspace"
        lock = _load_lock(ws)
        local = parse_local_packages(lock)
        for pkg in local:
            assert _is_flat_package(ws, local, pkg) is False


class TestWorkspaceFlat:
    """Tests using a workspace where my-lib and my-core use flat layout (no src/)."""

    ws_root = FIXTURES / "workspace-flat"
    lock_data = _load_lock(ws_root)

    def test_parse_local_packages(self):
        result = parse_local_packages(self.lock_data)
        assert result == {
            "my-app": "my-app",
            "my-lib": "my-lib",
            "my-core": "my-core",
        }

    def test_find_transitive_from_app(self):
        result = find_transitive_local_deps(self.lock_data, "my-app")
        assert result == {
            "my-app": "my-app",
            "my-lib": "my-lib",
            "my-core": "my-core",
        }

    def test_flat_layout_detection(self):
        """my-lib and my-core use flat layout (no src/), my-app uses src layout."""
        local = parse_local_packages(self.lock_data)
        for name, path in local.items():
            module = name.replace("-", "_")
            src_init = self.ws_root / path / "src" / module / "__init__.py"
            flat_init = self.ws_root / path / module / "__init__.py"
            if name == "my-app":
                assert src_init.exists(), f"{name} should have src layout"
                assert not flat_init.exists()
            else:
                assert not src_init.exists(), f"{name} should have flat layout"
                assert flat_init.exists()

    def test_all_have_build_system(self):
        """All packages in workspace-flat have [build-system], unlike workspace-app."""
        local = parse_local_packages(self.lock_data)
        for name, path in local.items():
            toml = tomllib.loads((self.ws_root / path / "pyproject.toml").read_text())
            assert "build-system" in toml, f"{name} should have build-system"

    def test_not_flat_package(self):
        """No package should be detected as flat-package (no build-system)."""
        local = parse_local_packages(self.lock_data)
        for pkg in local:
            assert _is_flat_package(self.ws_root, local, pkg) is False


class TestBuildUvSyncArgs:
    """Tests for `uv sync` argv construction — mirrors uv CLI flags verbatim."""

    def _args(self, **overrides):
        defaults = dict(
            package=None,
            extras=[],
            groups=[],
            all_extras=False,
            all_groups=False,
            all_packages=False,
        )
        return build_uv_sync_args(**{**defaults, **overrides})

    def test_bare_defaults(self):
        assert self._args() == ["uv", "sync", "--frozen", "--link-mode", "copy"]

    def test_all_extras_flag(self):
        assert self._args(all_extras=True) == [
            "uv",
            "sync",
            "--frozen",
            "--link-mode",
            "copy",
            "--all-extras",
        ]

    def test_all_groups_flag(self):
        assert self._args(all_groups=True) == [
            "uv",
            "sync",
            "--frozen",
            "--link-mode",
            "copy",
            "--all-groups",
        ]

    def test_all_packages_flag(self):
        assert self._args(all_packages=True) == [
            "uv",
            "sync",
            "--frozen",
            "--link-mode",
            "copy",
            "--all-packages",
        ]

    def test_repeated_extras(self):
        assert self._args(extras=["gpu", "viz"]) == [
            "uv",
            "sync",
            "--frozen",
            "--link-mode",
            "copy",
            "--extra",
            "gpu",
            "--extra",
            "viz",
        ]

    def test_repeated_groups(self):
        assert self._args(groups=["dev", "docs"]) == [
            "uv",
            "sync",
            "--frozen",
            "--link-mode",
            "copy",
            "--group",
            "dev",
            "--group",
            "docs",
        ]

    def test_package(self):
        assert self._args(package="my-app") == [
            "uv",
            "sync",
            "--frozen",
            "--link-mode",
            "copy",
            "--package",
            "my-app",
        ]

    def test_all_together(self):
        args = self._args(
            package="my-app",
            extras=["gpu"],
            groups=["dev", "docs"],
            all_extras=True,
            all_groups=True,
            all_packages=True,
        )
        assert args == [
            "uv",
            "sync",
            "--frozen",
            "--link-mode",
            "copy",
            "--all-extras",
            "--extra",
            "gpu",
            "--all-groups",
            "--group",
            "dev",
            "--group",
            "docs",
            "--all-packages",
            "--package",
            "my-app",
        ]


class TestMatchReachable:
    """Unit tests for _match_reachable — the pure path-matching logic behind _filter_reachable."""

    def test_root_package_at_dot(self):
        """A package at '.' with workspace_path='.' must survive filtering.

        Regression: posixpath.dirname('pyproject.toml') returns '' while
        normpath('.') returns '.', so the root package was silently dropped.
        """
        packages = OrderedDict([("standalone-app", ".")])
        result = _match_reachable(packages, ".", ["pyproject.toml", "src/standalone_app/__init__.py"])
        assert result == packages

    def test_keeps_sibling_within_source(self):
        packages = OrderedDict([("encode", "../Encode"), ("l1-lib", "../L1_Lib")])
        result = _match_reachable(
            packages,
            "Icebeam",
            [
                "Icebeam/pyproject.toml",
                "Encode/pyproject.toml",
                "L1_Lib/pyproject.toml",
            ],
        )
        assert result == packages

    def test_drops_paths_not_in_source(self):
        packages = OrderedDict(
            [
                ("encode", "../Encode"),
                ("auto-quest", "../../TypeSafe/projects/AutoQuest"),
            ]
        )
        result = _match_reachable(packages, "Icebeam", ["Icebeam/pyproject.toml", "Encode/pyproject.toml"])
        assert result == OrderedDict([("encode", "../Encode")])

    def test_subproject_root_package(self):
        """The subproject itself (at '.') should be kept when workspace_path is a subdirectory."""
        packages = OrderedDict([("sub-project", "."), ("my-dep", "../my-dep")])
        result = _match_reachable(
            packages,
            "sub-project",
            ["sub-project/pyproject.toml", "my-dep/pyproject.toml"],
        )
        assert result == packages

    def test_preserves_order(self):
        packages = OrderedDict([("a", "."), ("b", "../gone"), ("c", "sub")])
        result = _match_reachable(packages, "ws", ["ws/pyproject.toml", "ws/sub/pyproject.toml"])
        assert list(result.keys()) == ["a", "c"]


class TestModuleName:
    """_module_name maps distribution names to Python module names."""

    def test_standard_hyphenated(self):
        assert _module_name("my-app") == "my_app"

    def test_standard_underscored(self):
        assert _module_name("my_lib") == "my_lib"

    def test_dagger_io_override(self):
        assert _module_name("dagger-io") == "dagger"


class TestNameNormalization:
    """PEP 503 name normalization: underscores, mixed case, dots all map to hyphens."""

    def test_underscore_to_hyphen(self):
        assert _normalize("frens_in_common") == "frens-in-common"

    def test_already_normalized(self):
        assert _normalize("my-app") == "my-app"

    def test_mixed_separators(self):
        assert _normalize("My_Package.Name") == "my-package-name"

    def test_consecutive_separators(self):
        assert _normalize("a__b--c..d") == "a-b-c-d"

    def test_find_transitive_with_underscored_name(self):
        """Passing an underscored project name should still resolve against
        the hyphenated lockfile keys."""
        lock_data = _load_lock(FIXTURES / "workspace")
        result = find_transitive_local_deps(lock_data, "my_app")
        assert result == {
            "my-app": "my-app",
            "my-lib": "my-lib",
            "my-core": "my-core",
        }

    def test_find_transitive_with_uppercase_name(self):
        lock_data = _load_lock(FIXTURES / "workspace")
        result = find_transitive_local_deps(lock_data, "My-App")
        assert result == {
            "my-app": "my-app",
            "my-lib": "my-lib",
            "my-core": "my-core",
        }
