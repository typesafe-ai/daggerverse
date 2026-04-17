"""Tests for uv.lock parsing and transitive dependency resolution."""

import tomllib
from collections import OrderedDict
from pathlib import Path

from uv_workspace._utils import (
    build_uv_sync_args,
    find_transitive_local_deps,
    parse_local_packages,
)

FIXTURES = Path(__file__).parent / "_packages"


def _load_lock(path: Path) -> dict:
    return tomllib.loads((path / "uv.lock").read_text())


class TestWorkspace:
    """Tests using a workspace with my-app -> my-lib -> my-core."""

    lock_data = _load_lock(FIXTURES / "workspace")

    def testparse_local_packages(self):
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


class TestStandalone:
    """Tests using a standalone single-package project."""

    lock_data = _load_lock(FIXTURES / "standalone-app")

    def testparse_local_packages(self):
        result = parse_local_packages(self.lock_data)
        assert result == {"standalone-app": "."}

    def test_find_transitive(self):
        result = find_transitive_local_deps(self.lock_data, "standalone-app")
        assert result == {"standalone-app": "."}

    def test_find_transitive_unknown_package(self):
        result = find_transitive_local_deps(self.lock_data, "nonexistent")
        assert result == {}


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
        assert self._args() == ["uv", "sync"]

    def test_all_extras_flag(self):
        assert self._args(all_extras=True) == ["uv", "sync", "--all-extras"]

    def test_all_groups_flag(self):
        assert self._args(all_groups=True) == ["uv", "sync", "--all-groups"]

    def test_all_packages_flag(self):
        assert self._args(all_packages=True) == ["uv", "sync", "--all-packages"]

    def test_repeated_extras(self):
        assert self._args(extras=["gpu", "viz"]) == [
            "uv",
            "sync",
            "--extra",
            "gpu",
            "--extra",
            "viz",
        ]

    def test_repeated_groups(self):
        assert self._args(groups=["dev", "docs"]) == [
            "uv",
            "sync",
            "--group",
            "dev",
            "--group",
            "docs",
        ]

    def test_package(self):
        assert self._args(package="my-app") == ["uv", "sync", "--package", "my-app"]

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
