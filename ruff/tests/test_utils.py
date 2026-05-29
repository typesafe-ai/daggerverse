"""Tests for ruff version resolution utilities (pure logic, no Dagger runtime)."""

from pathlib import Path

from ruff.utils import (
    is_exact_version,
    minimal_compatible_version,
    normalize_exact_version,
    parse_version_from_pyproject,
    parse_version_from_ruff_toml,
    parse_version_from_uv_lock,
    resolve_specifier,
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

    def test_equals_wildcard_is_not_exact(self):
        assert is_exact_version("==0.5.*") is False


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


class TestMinimalCompatibleVersion:
    def test_gte_lt_range_picks_lower_bound(self):
        assert minimal_compatible_version(">=0.4.4,<0.5.0") == "0.4.4"

    def test_gte_only(self):
        assert minimal_compatible_version(">=0.6.0") == "0.6.0"

    def test_tilde_full(self):
        assert minimal_compatible_version("~=0.4.4") == "0.4.4"

    def test_tilde_pads_to_patch(self):
        assert minimal_compatible_version("~=0.5") == "0.5.0"

    def test_equals_wildcard_pads_to_patch(self):
        assert minimal_compatible_version("==0.5.*") == "0.5.0"

    def test_exclusive_gt_bumps_patch(self):
        assert minimal_compatible_version(">0.5.0") == "0.5.1"

    def test_excludes_lower_bound_via_not_equal(self):
        # `!=` on the lower bound forces the next satisfying candidate to win.
        assert minimal_compatible_version(">=0.4.4,!=0.4.4") is None

    def test_upper_bound_only_has_no_lower_bound(self):
        assert minimal_compatible_version("<1.0.0") is None

    def test_not_equal_only_has_no_lower_bound(self):
        assert minimal_compatible_version("!=0.3.0") is None


class TestResolveSpecifier:
    def test_bare_exact(self):
        assert resolve_specifier("0.4.4") == "0.4.4"

    def test_double_equals_exact(self):
        assert resolve_specifier("==0.5.0") == "0.5.0"

    def test_range_uses_minimal(self):
        assert resolve_specifier(">=0.6.0,<0.7.0") == "0.6.0"

    def test_no_lower_bound_falls_back_to_default(self):
        assert resolve_specifier("<1.0.0") == "latest"
