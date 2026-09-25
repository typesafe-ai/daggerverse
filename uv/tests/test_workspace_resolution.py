"""Tests for uv.lock parsing and transitive dependency resolution."""

import asyncio
import posixpath
import tomllib
from collections import OrderedDict
from pathlib import Path
from typing import ClassVar

from uv.utils import (
    build_uv_sync_args,
    find_transitive_local_deps,
    normalize_package_name,
    parse_local_packages,
)
from uv.workspace.plan import _match_reachable, _module_name

FIXTURES = Path(__file__).parent / "_packages"


class _FakeFile:
    def __init__(self, contents: str):
        self._contents = contents

    async def contents(self) -> str:
        return self._contents


class _PlanDirectory:
    def __init__(self, files: dict[str, str], pyproject_paths: list[str]):
        self._files = files
        self._pyproject_paths = pyproject_paths

    def directory(self, path: str):
        return self

    def file(self, path: str) -> _FakeFile:
        return _FakeFile(self._files[path])

    async def glob(self, pattern: str) -> list[str]:
        assert pattern == "**/pyproject.toml"
        return self._pyproject_paths

    def with_directory(self, path: str, directory):
        return self


def _codegen_visits(
    monkeypatch,
    lock: str,
    pyproject: str,
    pyproject_paths: list[str] | None = None,
    **selection,
) -> list[str]:
    from uv.workspace import plan
    from uv.workspace.plan import UvSyncPlan

    visited: list[str] = []

    async def record_codegen(ws_dir, codegen_path: str):
        visited.append(codegen_path)
        return ws_dir

    async def skip_discovery(*args, **kwargs):
        return OrderedDict(), OrderedDict(), {}

    monkeypatch.setattr(plan, "_run_codegen", record_codegen)
    monkeypatch.setattr(plan, "_discover_local_packages", skip_discovery)
    local_paths = parse_local_packages(tomllib.loads(lock)).values()
    if pyproject_paths is None:
        pyproject_paths = ["pyproject.toml", *(posixpath.join(path, "pyproject.toml") for path in local_paths)]
    source = _PlanDirectory(
        {"uv.lock": lock, "pyproject.toml": pyproject},
        list(dict.fromkeys(pyproject_paths)),
    )
    asyncio.run(UvSyncPlan.create(source_dir=source, dagger_codegen=True, **selection))
    return visited


class TestCodegenPackageSelection:
    workspace_lock = """version = 1
[[package]]
name = "app-one"
source = { editable = "packages/app-one" }
[[package]]
name = "app-two"
source = { editable = "packages/app-two" }
"""
    pure_workspace = '[tool.uv.workspace]\nmembers = ["packages/*"]\n'

    def test_all_packages_visits_each_local_member(self, monkeypatch):
        assert _codegen_visits(
            monkeypatch,
            self.workspace_lock,
            self.pure_workspace,
            all_packages=True,
        ) == ["packages/app-one", "packages/app-two"]

    def test_all_packages_skips_unreachable_lock_entries(self, monkeypatch):
        lock = (
            self.workspace_lock
            + """[[package]]
name = "outside-source"
source = { editable = "../outside-source" }
"""
        )
        assert _codegen_visits(
            monkeypatch,
            lock,
            self.pure_workspace,
            pyproject_paths=[
                "pyproject.toml",
                "packages/app-one/pyproject.toml",
                "packages/app-two/pyproject.toml",
            ],
            all_packages=True,
        ) == ["packages/app-one", "packages/app-two"]

    def test_explicit_packages_preserve_selection_and_deduplicate(self, monkeypatch):
        assert _codegen_visits(
            monkeypatch,
            self.workspace_lock,
            self.pure_workspace,
            package=["app-two", "app-one", "app-two"],
        ) == ["packages/app-two", "packages/app-one"]

    def test_default_package_preserves_workspace_root_codegen(self, monkeypatch):
        standalone_lock = """version = 1
[[package]]
name = "root-app"
source = { editable = "." }
"""
        assert _codegen_visits(
            monkeypatch,
            standalone_lock,
            '[project]\nname = "root-app"\n',
        ) == ["."]


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
            "test-ws": ".",
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

    def test_includes_virtual_root(self):
        """Virtual metadata must be available when staging the workspace."""
        result = parse_local_packages(self.lock_data)
        assert result["test-ws"] == "."

    def test_parse_returns_ordered_dict(self):
        result = parse_local_packages(self.lock_data)
        assert isinstance(result, OrderedDict)

    def test_parse_sorted_order(self):
        result = parse_local_packages(self.lock_data)
        assert list(result.keys()) == ["my-app", "my-core", "my-lib", "test-ws"]

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


class TestVirtualWorkspaceRoot:
    """A virtual workspace root (source = virtual) still pulls in its members.

    Virtual members must be scaffolded and traversed, including when their
    dependencies are reached through another virtual member.
    """

    lock_data: ClassVar = {
        "package": [
            {
                "name": "the-root",
                "source": {"virtual": "."},
                "dependencies": [{"name": "alarms"}, {"name": "networking"}],
            },
            {"name": "alarms", "source": {"virtual": "alarms"}, "dependencies": [{"name": "networking"}]},
            {"name": "networking", "source": {"editable": "networking"}},
        ]
    }

    def test_virtual_metadata_is_local(self):
        assert parse_local_packages(self.lock_data) == {"the-root": ".", "alarms": "alarms", "networking": "networking"}

    def test_transitive_from_virtual_root(self):
        result = find_transitive_local_deps(self.lock_data, "the-root")
        assert result == {"the-root": ".", "alarms": "alarms", "networking": "networking"}

    def test_transitive_through_virtual_member(self):
        assert find_transitive_local_deps(self.lock_data, "alarms") == {"alarms": "alarms", "networking": "networking"}


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
            "test-ws-app": ".",
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
            if local[pkg] == ".":
                continue
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
            "test-ws-flat": ".",
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
            if path == ".":
                continue
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
            if path == ".":
                continue
            toml = tomllib.loads((self.ws_root / path / "pyproject.toml").read_text())
            assert "build-system" in toml, f"{name} should have build-system"

    def test_not_flat_package(self):
        """No package should be detected as flat-package (no build-system)."""
        local = parse_local_packages(self.lock_data)
        for pkg in local:
            if local[pkg] == ".":
                continue
            assert _is_flat_package(self.ws_root, local, pkg) is False


class TestBuildUvSyncArgs:
    """Tests for `uv sync` argv construction — mirrors uv CLI flags verbatim."""

    def _args(self, **overrides):
        defaults = {
            "packages": [],
            "extras": [],
            "groups": [],
            "all_extras": False,
            "all_groups": False,
            "all_packages": False,
        }
        return build_uv_sync_args(**{**defaults, **overrides})

    def test_bare_defaults(self):
        assert self._args() == ["uv", "sync", "--frozen", "--link-mode", "copy"]

    def test_no_editable_flag(self):
        assert self._args(no_editable=True) == [
            "uv",
            "sync",
            "--frozen",
            "--link-mode",
            "copy",
            "--no-editable",
        ]

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
        assert self._args(packages=["my-app"]) == [
            "uv",
            "sync",
            "--frozen",
            "--link-mode",
            "copy",
            "--package",
            "my-app",
        ]

    def test_repeated_packages(self):
        assert self._args(packages=["my-app", "my-lib"]) == [
            "uv",
            "sync",
            "--frozen",
            "--link-mode",
            "copy",
            "--package",
            "my-app",
            "--package",
            "my-lib",
        ]

    def test_all_together(self):
        args = self._args(
            packages=["my-app"],
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
        assert normalize_package_name("frens_in_common") == "frens-in-common"

    def test_already_normalized(self):
        assert normalize_package_name("my-app") == "my-app"

    def test_mixed_separators(self):
        assert normalize_package_name("My_Package.Name") == "my-package-name"

    def test_consecutive_separators(self):
        assert normalize_package_name("a__b--c..d") == "a-b-c-d"

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


class TestNestedWorkspacePathResolution:
    """Regression: when workspace_path is a subdirectory, workspace-relative
    package paths (including ``..``) must resolve to valid source-root-relative
    paths for both container writes and source reads.

    Before the fix, the container placed ``pyproject.toml``/``uv.lock`` at
    ``/work/`` regardless of nesting depth, so ``../../Soar`` in a lock file
    would resolve to ``/work/../../Soar`` — escaping the filesystem root.
    """

    lock_data = _load_lock(FIXTURES / "partial-workspace" / "sub-project")

    def test_sibling_dep_resolves_to_source_root(self):
        """``../my-dep`` from ``sub-project`` should resolve to ``my-dep``."""
        local = parse_local_packages(self.lock_data)
        assert local["my-dep"] == "../my-dep"
        resolved = posixpath.normpath(posixpath.join("sub-project", "../my-dep"))
        assert resolved == "my-dep"

    def test_deep_dep_resolves_to_source_root(self):
        """``../../gone/ext-pkg`` from ``sub-project`` should resolve to ``../gone/ext-pkg``
        (outside source tree — filtered by _match_reachable)."""
        local = parse_local_packages(self.lock_data)
        assert local["ext-pkg"] == "../../gone/ext-pkg"
        resolved = posixpath.normpath(posixpath.join("sub-project", "../../gone/ext-pkg"))
        assert resolved == "../gone/ext-pkg"

    def test_container_path_normalization(self):
        """Container paths with ``..`` normalize correctly when workdir mirrors nesting."""
        workdir = "/work/sub-project"
        pkg_path = "../my-dep"
        container_path = posixpath.normpath(posixpath.join(workdir, pkg_path, "pyproject.toml"))
        assert container_path == "/work/my-dep/pyproject.toml"

    def test_container_path_deep_dep(self):
        workdir = "/work/sub-project"
        pkg_path = "../../gone/ext-pkg"
        container_path = posixpath.normpath(posixpath.join(workdir, pkg_path, "pyproject.toml"))
        assert container_path == "/gone/ext-pkg/pyproject.toml"

    def test_root_workspace_paths_unchanged(self):
        """When workspace_path is '.', paths pass through unchanged."""
        ws_lock = _load_lock(FIXTURES / "workspace")
        local = parse_local_packages(ws_lock)
        for path in local.values():
            resolved = posixpath.normpath(posixpath.join(".", path))
            assert resolved == path
