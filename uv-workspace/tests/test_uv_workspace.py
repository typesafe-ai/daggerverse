"""Tests for uv.lock parsing and transitive dependency resolution."""

import tomllib
from collections import OrderedDict
from pathlib import Path

from flow.uv_workspace import _find_transitive_local_deps, _parse_local_packages

FIXTURES = Path(__file__).parent / "_packages"


def _load_lock(path: Path) -> dict:
    return tomllib.loads((path / "uv.lock").read_text())


class TestWorkspace:
    """Tests using a workspace with my-app -> my-lib -> my-core."""

    lock_data = _load_lock(FIXTURES / "workspace")

    def test_parse_local_packages(self):
        result = _parse_local_packages(self.lock_data)
        assert result == {
            "my-app": "my-app",
            "my-lib": "my-lib",
            "my-core": "my-core",
        }

    def test_find_transitive_from_app(self):
        result = _find_transitive_local_deps(self.lock_data, "my-app")
        assert result == {
            "my-app": "my-app",
            "my-lib": "my-lib",
            "my-core": "my-core",
        }

    def test_find_transitive_from_lib(self):
        result = _find_transitive_local_deps(self.lock_data, "my-lib")
        assert result == {
            "my-lib": "my-lib",
            "my-core": "my-core",
        }

    def test_find_transitive_from_leaf(self):
        result = _find_transitive_local_deps(self.lock_data, "my-core")
        assert result == {"my-core": "my-core"}

    def test_skips_virtual_root(self):
        """The workspace root (source = virtual) should not appear in local packages."""
        result = _parse_local_packages(self.lock_data)
        assert "test-ws" not in result

    def test_parse_returns_ordered_dict(self):
        result = _parse_local_packages(self.lock_data)
        assert isinstance(result, OrderedDict)

    def test_parse_sorted_order(self):
        result = _parse_local_packages(self.lock_data)
        assert list(result.keys()) == ["my-app", "my-core", "my-lib"]

    def test_transitive_returns_ordered_dict(self):
        result = _find_transitive_local_deps(self.lock_data, "my-app")
        assert isinstance(result, OrderedDict)

    def test_transitive_sorted_order(self):
        result = _find_transitive_local_deps(self.lock_data, "my-app")
        assert list(result.keys()) == ["my-app", "my-core", "my-lib"]

    def test_transitive_from_lib_sorted_order(self):
        result = _find_transitive_local_deps(self.lock_data, "my-lib")
        assert list(result.keys()) == ["my-core", "my-lib"]


class TestStandalone:
    """Tests using a standalone single-package project."""

    lock_data = _load_lock(FIXTURES / "standalone-app")

    def test_parse_local_packages(self):
        result = _parse_local_packages(self.lock_data)
        assert result == {"standalone-app": "."}

    def test_find_transitive(self):
        result = _find_transitive_local_deps(self.lock_data, "standalone-app")
        assert result == {"standalone-app": "."}

    def test_find_transitive_unknown_package(self):
        result = _find_transitive_local_deps(self.lock_data, "nonexistent")
        assert result == {}
