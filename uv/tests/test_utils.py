"""Tests for the uv module's pure helpers (no Dagger runtime)."""

import pytest

from uv.utils import (
    debian_image_ref,
    format_audit_failure,
    image_ref,
    is_exact_version,
    is_excluded,
    minimal_compatible_version,
    normalize_exact_version,
    parse_indices,
    parse_project_name,
    parse_pyvenv_cfg,
    parse_required_version_from_pyproject,
    parse_required_version_from_uv_toml,
    require_package_selection,
    resolve_specifier,
    workspace_path,
)
from uv.workspace.index import UvIndex, merge_indices


class TestRequirePackageSelection:
    def test_current_package_ok(self):
        require_package_selection([], False, "my-app")  # current package -> no raise

    def test_explicit_packages_ok(self):
        require_package_selection(["my-app"], False, None)

    def test_all_packages_ok(self):
        require_package_selection([], True, None)

    def test_pure_root_no_selection_raises(self):
        with pytest.raises(ValueError, match="pure workspace root"):
            require_package_selection([], False, None)


class TestImageRef:
    def test_points_at_astral_uv(self):
        assert image_ref("0.9.6") == "ghcr.io/astral-sh/uv:0.9.6"

    def test_latest(self):
        assert image_ref("latest") == "ghcr.io/astral-sh/uv:latest"


class TestDebianImageRef:
    def test_latest_resolves_to_pinned_version(self):
        # `latest` must not float — it maps to a concrete pinned version.
        assert debian_image_ref("latest") == "ghcr.io/astral-sh/uv:0.11.19-debian"

    def test_version_pinned(self):
        assert debian_image_ref("0.9.6") == "ghcr.io/astral-sh/uv:0.9.6-debian"


class TestParsePyvenvCfg:
    def test_parses_keys(self):
        cfg = "home = /root/.local/share/uv/python/cpython-3.13.7-linux-aarch64-gnu/bin\nrelocatable = true\nversion = 3.13.7\n"
        parsed = parse_pyvenv_cfg(cfg)
        assert parsed["home"] == "/root/.local/share/uv/python/cpython-3.13.7-linux-aarch64-gnu/bin"
        assert parsed["relocatable"] == "true"
        assert parsed["version"] == "3.13.7"

    def test_ignores_blank_and_non_kv_lines(self):
        assert parse_pyvenv_cfg("\n# comment without equals\nkey = value\n") == {"key": "value"}


class TestParseProjectName:
    def test_reads_project_name(self):
        assert parse_project_name('[project]\nname = "my-app"\nversion = "0"\n') == "my-app"

    def test_missing_project(self):
        assert parse_project_name("[tool.uv.workspace]\nmembers = []\n") is None


class TestWorkspacePath:
    def test_root(self):
        assert workspace_path("uv.lock") == "."

    def test_nested(self):
        assert workspace_path("pkgs/app/uv.lock") == "pkgs/app"


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


class TestParseIndices:
    def test_pyproject_toml(self):
        content = '[[tool.uv.index]]\nname = "pytorch"\nurl = "https://download.pytorch.org/whl/cpu"\n'
        result = parse_indices(content)
        assert len(result) == 1
        assert result[0]["name"] == "pytorch"
        assert result[0]["url"] == "https://download.pytorch.org/whl/cpu"
        assert result[0]["publish_url"] is None

    def test_uv_toml(self):
        content = '[[index]]\nname = "internal"\nurl = "https://pypi.internal.dev/simple"\n'
        result = parse_indices(content, uv_toml=True)
        assert len(result) == 1
        assert result[0]["name"] == "internal"
        assert result[0]["url"] == "https://pypi.internal.dev/simple"

    def test_publish_url(self):
        content = '[[tool.uv.index]]\nname = "corp"\nurl = "https://corp.dev/simple"\npublish-url = "https://corp.dev/upload"\n'
        result = parse_indices(content)
        assert result[0]["publish_url"] == "https://corp.dev/upload"

    def test_multiple_indices_sorted_by_name(self):
        content = (
            '[[tool.uv.index]]\nname = "zeta"\nurl = "https://z.dev"\n\n'
            '[[tool.uv.index]]\nname = "alpha"\nurl = "https://a.dev"\n'
        )
        result = parse_indices(content)
        assert [e["name"] for e in result] == ["alpha", "zeta"]

    def test_skips_entries_without_name(self):
        content = '[[tool.uv.index]]\nurl = "https://unnamed.dev"\n'
        assert parse_indices(content) == []

    def test_skips_entries_without_url(self):
        content = '[[tool.uv.index]]\nname = "no-url"\n'
        assert parse_indices(content) == []

    def test_empty(self):
        assert parse_indices('[project]\nname = "x"\n') == []

    def test_default_flag(self):
        content = '[[tool.uv.index]]\nname = "corp"\nurl = "https://corp.dev"\ndefault = true\n'
        result = parse_indices(content)
        assert result[0]["default"] is True

    def test_explicit_flag(self):
        content = '[[tool.uv.index]]\nname = "corp"\nurl = "https://corp.dev"\nexplicit = true\n'
        result = parse_indices(content)
        assert result[0]["explicit"] is True

    def test_authenticate(self):
        content = '[[tool.uv.index]]\nname = "corp"\nurl = "https://corp.dev"\nauthenticate = "always"\n'
        result = parse_indices(content)
        assert result[0]["authenticate"] == "always"

    def test_format_flat(self):
        content = '[[tool.uv.index]]\nname = "local"\nurl = "file:///wheels"\nformat = "flat"\n'
        result = parse_indices(content)
        assert result[0]["format"] == "flat"

    def test_defaults_for_optional_fields(self):
        content = '[[tool.uv.index]]\nname = "plain"\nurl = "https://plain.dev"\n'
        result = parse_indices(content)
        assert result[0]["default"] is False
        assert result[0]["explicit"] is False
        assert result[0]["authenticate"] is None
        assert result[0]["format"] is None


class TestMergeIndices:
    def test_base_only(self):
        base = [{"name": "alpha", "url": "https://a.dev", "publish_url": None}]
        result = merge_indices(base, [])
        assert result == [UvIndex(name="alpha", url="https://a.dev", publish_url=None)]

    def test_override_only(self):
        override = [{"name": "beta", "url": "https://b.dev", "publish_url": None}]
        result = merge_indices([], override)
        assert result == [UvIndex(name="beta", url="https://b.dev", publish_url=None)]

    def test_override_wins_on_name_collision(self):
        base = [{"name": "corp", "url": "https://old.dev", "publish_url": None}]
        override = [{"name": "corp", "url": "https://new.dev", "publish_url": "https://new.dev/upload"}]
        result = merge_indices(base, override)
        assert len(result) == 1
        assert result[0].url == "https://new.dev"
        assert result[0].publish_url == "https://new.dev/upload"

    def test_disjoint_sets_merge_sorted(self):
        base = [{"name": "zeta", "url": "https://z.dev", "publish_url": None}]
        override = [{"name": "alpha", "url": "https://a.dev", "publish_url": None}]
        result = merge_indices(base, override)
        assert [r.name for r in result] == ["alpha", "zeta"]

    def test_mixed_overlap_and_unique(self):
        base = [
            {"name": "alpha", "url": "https://a.dev", "publish_url": None},
            {"name": "shared", "url": "https://old.dev", "publish_url": None},
        ]
        override = [
            {"name": "beta", "url": "https://b.dev", "publish_url": None},
            {"name": "shared", "url": "https://new.dev", "publish_url": None},
        ]
        result = merge_indices(base, override)
        assert [r.name for r in result] == ["alpha", "beta", "shared"]
        shared = next(r for r in result if r.name == "shared")
        assert shared.url == "https://new.dev"

    def test_empty_both(self):
        assert merge_indices([], []) == []

    def test_preserves_extra_fields(self):
        base = [
            {
                "name": "corp",
                "url": "https://corp.dev",
                "publish_url": None,
                "default": True,
                "explicit": False,
                "authenticate": "always",
                "format": None,
            }
        ]
        result = merge_indices(base, [])
        assert result[0].default is True
        assert result[0].authenticate == "always"

    def test_override_replaces_all_fields(self):
        base = [
            {
                "name": "corp",
                "url": "https://old.dev",
                "publish_url": None,
                "default": False,
                "explicit": False,
                "authenticate": None,
                "format": None,
            }
        ]
        override = [
            {
                "name": "corp",
                "url": "https://new.dev",
                "publish_url": None,
                "default": True,
                "explicit": True,
                "authenticate": "always",
                "format": "flat",
            }
        ]
        result = merge_indices(base, override)
        assert result[0].url == "https://new.dev"
        assert result[0].default is True
        assert result[0].explicit is True
        assert result[0].authenticate == "always"
        assert result[0].format == "flat"


class TestResolveSpecifier:
    def test_bare_exact(self):
        assert resolve_specifier("0.5.0") == "0.5.0"

    def test_double_equals_exact(self):
        assert resolve_specifier("==0.5.0") == "0.5.0"

    def test_range_uses_minimal(self):
        assert resolve_specifier(">=0.4.0,<0.6.0") == "0.4.0"

    def test_no_lower_bound_falls_back_to_default(self):
        assert resolve_specifier("<1.0.0") == "latest"
