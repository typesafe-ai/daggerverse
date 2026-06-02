"""Tests for the uv module's pure helpers (no Dagger runtime)."""

from uv.utils import (
    format_audit_failure,
    image_ref,
    is_exact_version,
    is_excluded,
    minimal_compatible_version,
    normalize_exact_version,
    parse_required_version_from_pyproject,
    parse_required_version_from_uv_toml,
    pyproject_path,
    resolve_specifier,
    uv_toml_path,
    workspace_path,
)


class TestImageRef:
    def test_points_at_astral_uv(self):
        assert image_ref("0.9.6") == "ghcr.io/astral-sh/uv:0.9.6"

    def test_latest(self):
        assert image_ref("latest") == "ghcr.io/astral-sh/uv:latest"


class TestWorkspacePath:
    def test_root(self):
        assert workspace_path("uv.lock") == "."

    def test_nested(self):
        assert workspace_path("pkgs/app/uv.lock") == "pkgs/app"


class TestPyprojectPath:
    def test_root(self):
        assert pyproject_path("uv.lock") == "pyproject.toml"

    def test_nested(self):
        assert pyproject_path("pkgs/app/uv.lock") == "pkgs/app/pyproject.toml"


class TestUvTomlPath:
    def test_root(self):
        assert uv_toml_path("uv.lock") == "uv.toml"

    def test_nested(self):
        assert uv_toml_path("pkgs/app/uv.lock") == "pkgs/app/uv.toml"


class TestIsExcluded:
    def test_no_patterns(self):
        assert is_excluded("uv-workspace", []) is False

    def test_exact_match(self):
        assert is_excluded(".dagger", [".dagger"]) is True

    def test_globstar_match(self):
        assert is_excluded("uv-workspace/tests/_packages/app", ["**/tests/_packages/**"]) is True

    def test_no_match(self):
        assert is_excluded("uv-workspace", ["**/tests/**"]) is False

    def test_any_of_several(self):
        assert is_excluded("a/b", ["x/*", "a/*"]) is True


class TestIsExactVersion:
    def test_bare(self):
        assert is_exact_version("0.5.0") is True

    def test_double_equals(self):
        assert is_exact_version("==0.5.0") is True

    def test_gte(self):
        assert is_exact_version(">=0.5.0") is False

    def test_range(self):
        assert is_exact_version(">=0.5.0,<0.6.0") is False

    def test_equals_wildcard_is_not_exact(self):
        assert is_exact_version("==0.5.*") is False


class TestNormalizeExactVersion:
    def test_bare(self):
        assert normalize_exact_version("0.5.0") == "0.5.0"

    def test_double_equals_with_spaces(self):
        assert normalize_exact_version("== 0.5.0 ") == "0.5.0"


class TestParseRequiredVersion:
    def test_pyproject_tool_uv(self):
        content = '[tool.uv]\nrequired-version = ">=0.5.0"\n'
        assert parse_required_version_from_pyproject(content) == ">=0.5.0"

    def test_pyproject_missing(self):
        content = '[project]\nname = "x"\n'
        assert parse_required_version_from_pyproject(content) is None

    def test_uv_toml_top_level(self):
        assert parse_required_version_from_uv_toml('required-version = "==0.5.0"\n') == "==0.5.0"

    def test_uv_toml_missing(self):
        assert parse_required_version_from_uv_toml('index-strategy = "first-index"\n') is None


class TestMinimalCompatibleVersion:
    def test_gte_lt_range_picks_lower_bound(self):
        assert minimal_compatible_version(">=0.4.0,<0.6.0") == "0.4.0"

    def test_gte_only(self):
        assert minimal_compatible_version(">=0.6.0") == "0.6.0"

    def test_tilde_pads_to_patch(self):
        assert minimal_compatible_version("~=0.5") == "0.5.0"

    def test_equals_wildcard_pads_to_patch(self):
        assert minimal_compatible_version("==0.5.*") == "0.5.0"

    def test_exclusive_gt_bumps_patch(self):
        assert minimal_compatible_version(">0.5.0") == "0.5.1"

    def test_upper_bound_only_has_no_lower_bound(self):
        assert minimal_compatible_version("<1.0.0") is None


class TestFormatAuditFailure:
    def test_includes_stderr_report(self):
        msg = format_audit_failure(1, "", "found 1 vulnerability in urllib3")
        assert "exit code 1" in msg
        assert "found 1 vulnerability in urllib3" in msg

    def test_includes_stdout_report(self):
        msg = format_audit_failure(2, "vulnerable: urllib3 2.6.3", "")
        assert "exit code 2" in msg
        assert "vulnerable: urllib3 2.6.3" in msg

    def test_combines_both_streams(self):
        msg = format_audit_failure(1, "stdout report", "stderr report")
        assert "stdout report" in msg
        assert "stderr report" in msg

    def test_deduplicates_identical_streams(self):
        msg = format_audit_failure(1, "same report", "same report")
        assert msg.count("same report") == 1

    def test_strips_whitespace(self):
        msg = format_audit_failure(1, "", "  report  \n")
        assert msg.endswith("report")

    def test_no_output_falls_back_to_summary(self):
        assert format_audit_failure(1, "", "") == "uv audit failed (exit code 1)"
        assert format_audit_failure(1, "   ", "\n") == "uv audit failed (exit code 1)"

    def test_names_workspace_in_summary(self):
        msg = format_audit_failure(1, "", "found 1 vulnerability", workspace="pkgs/app")
        assert msg.startswith("uv audit failed for pkgs/app (exit code 1):")
        assert "found 1 vulnerability" in msg

    def test_names_workspace_without_output(self):
        assert format_audit_failure(1, "", "", workspace="pkgs/app") == "uv audit failed for pkgs/app (exit code 1)"


class TestResolveSpecifier:
    def test_bare_exact(self):
        assert resolve_specifier("0.5.0") == "0.5.0"

    def test_double_equals_exact(self):
        assert resolve_specifier("==0.5.0") == "0.5.0"

    def test_range_uses_minimal(self):
        assert resolve_specifier(">=0.4.0,<0.6.0") == "0.4.0"

    def test_no_lower_bound_falls_back_to_default(self):
        assert resolve_specifier("<1.0.0") == "latest"
