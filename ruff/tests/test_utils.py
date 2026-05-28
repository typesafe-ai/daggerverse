"""Tests for ruff version resolution utilities (pure logic, no Dagger runtime)."""

from pathlib import Path

import pytest

from ruff.utils import (
    is_exact_version,
    normalize_exact_version,
    parse_version_from_pyproject,
    parse_version_from_ruff_toml,
    parse_version_from_uv_lock,
    resolve_from_pypi_data,
)

FIXTURES = Path(__file__).parent / "_fixtures"


class TestIsExactVersion:
    def test_bare_version(self):
        assert is_exact_version("0.4.4") is True

    def test_double_equals(self):
        assert is_exact_version("==0.4.4") is True

    def test_gte(self):
        assert is_exact_version(">=0.4.4") is False

    def test_gte_lt_range(self):
        assert is_exact_version(">=0.4.4,<0.5.0") is False

    def test_tilde(self):
        assert is_exact_version("~=0.4.4") is False

    def test_not_equal(self):
        assert is_exact_version("!=0.3.0") is False

    def test_lt(self):
        assert is_exact_version("<1.0.0") is False


class TestNormalizeExactVersion:
    def test_bare(self):
        assert normalize_exact_version("0.4.4") == "0.4.4"

    def test_double_equals(self):
        assert normalize_exact_version("==0.4.4") == "0.4.4"

    def test_double_equals_with_spaces(self):
        assert normalize_exact_version("== 0.4.4 ") == "0.4.4"

    def test_bare_with_whitespace(self):
        assert normalize_exact_version(" 0.4.4 ") == "0.4.4"


class TestParseVersionFromUvLock:
    def test_ruff_pinned(self):
        content = (FIXTURES / "uv_lock_pinned.toml").read_text()
        assert parse_version_from_uv_lock(content) == "0.4.8"

    def test_no_ruff(self):
        content = (FIXTURES / "uv_lock_no_ruff.toml").read_text()
        assert parse_version_from_uv_lock(content) is None


class TestParseVersionFromRuffToml:
    def test_exact_pin(self):
        content = (FIXTURES / "ruff_toml_exact.toml").read_text()
        assert parse_version_from_ruff_toml(content) == "==0.5.0"

    def test_range_specifier(self):
        content = (FIXTURES / "ruff_toml_range.toml").read_text()
        assert parse_version_from_ruff_toml(content) == ">=0.4.4,<0.5.0"

    def test_no_required_version(self):
        content = (FIXTURES / "ruff_toml_bare.toml").read_text()
        assert parse_version_from_ruff_toml(content) is None


class TestParseVersionFromPyproject:
    def test_with_ruff_section(self):
        content = (FIXTURES / "pyproject_with_ruff.toml").read_text()
        assert parse_version_from_pyproject(content) == ">=0.6.0"

    def test_no_ruff_section(self):
        content = (FIXTURES / "pyproject_no_ruff.toml").read_text()
        assert parse_version_from_pyproject(content) is None


class TestResolveFromPypiData:
    @pytest.fixture
    def pypi_json(self):
        return (FIXTURES / "pypi_ruff_subset.json").read_text()

    def test_range_specifier(self, pypi_json):
        assert resolve_from_pypi_data(pypi_json, ">=0.4.4,<0.5.0") == "0.4.10"

    def test_gte_only(self, pypi_json):
        assert resolve_from_pypi_data(pypi_json, ">=0.6.0") == "0.15.14"

    def test_exact_match(self, pypi_json):
        assert resolve_from_pypi_data(pypi_json, "==0.5.0") == "0.5.0"

    def test_skips_yanked(self, pypi_json):
        assert resolve_from_pypi_data(pypi_json, ">=0.5.0,<0.5.3") == "0.5.2"

    def test_no_match_raises(self, pypi_json):
        with pytest.raises(ValueError, match="No ruff versions on PyPI match"):
            resolve_from_pypi_data(pypi_json, ">=99.0.0")

    def test_single_version(self, pypi_json):
        assert resolve_from_pypi_data(pypi_json, "==0.4.3") == "0.4.3"
