import json
from datetime import UTC, datetime

import pytest
from uv.audit import (
    EXEMPTION_MARKER,
    UpdateStrategy,
    Vulnerability,
    add_exemptions,
    cooldown_cutoff,
    has_user_exemption,
    locked_version,
    minimal_fix,
    parse_report,
    prune_exemptions,
    upgrade_spec,
    upload_cutoff,
)


def _vuln(id: str, fix_versions: list[str], version: str = "2.6.3") -> Vulnerability:
    return Vulnerability(id=id, package="urllib3", version=version, fix_versions=fix_versions)


class TestParseReport:
    def test_reads_vulnerabilities(self):
        report = {
            "vulnerabilities": [
                {
                    "dependency": {"name": "Some_Pkg", "version": "1.0"},
                    "id": "GHSA-1",
                    "fix_versions": ["1.1"],
                },
                {"dependency": {"name": "other", "version": "2.0"}, "id": "PYSEC-2", "fix_versions": None},
            ]
        }
        assert parse_report(json.dumps(report)) == [
            Vulnerability(id="GHSA-1", package="some-pkg", version="1.0", fix_versions=["1.1"]),
            Vulnerability(id="PYSEC-2", package="other", version="2.0", fix_versions=[]),
        ]


class TestMinimalFix:
    def test_highest_of_each_advisorys_lowest_fix(self):
        assert minimal_fix([_vuln("a", ["2.7.0"]), _vuln("b", ["2.8.0"])]) == "2.8.0"

    def test_skips_fixes_on_older_branches(self):
        assert minimal_fix([_vuln("a", ["1.26.20", "2.7.0"])]) == "2.7.0"

    def test_unfixed_advisory(self):
        assert minimal_fix([_vuln("a", ["2.7.0"]), _vuln("b", [])]) is None


class TestUpgradeSpec:
    def test_latest(self):
        assert upgrade_spec("urllib3", "2.8.0", UpdateStrategy.LATEST) == "urllib3"

    def test_minimal(self):
        assert upgrade_spec("urllib3", "2.8.0", UpdateStrategy.MINIMAL) == "urllib3==2.8.0"


_LOCK = """\
version = 1

[options]
exclude-newer = "0001-01-01T00:00:00Z"
exclude-newer-span = "P7D"

[[package]]
name = "urllib3"
version = "2.8.0"
sdist = { url = "u", hash = "h", size = 1, upload-time = "2026-09-15T19:29:36.253Z" }
wheels = [{ url = "u", hash = "h", size = 1, upload-time = "2026-09-15T19:29:34.577Z" }]
"""


class TestLock:
    def test_upload_cutoff_rounds_latest_file_up(self):
        assert upload_cutoff(_LOCK, "urllib3", "2.8.0") == "2026-09-15T19:29:37Z"

    def test_upload_cutoff_missing(self):
        with pytest.raises(ValueError, match="urllib3==2.9.0"):
            upload_cutoff(_LOCK, "urllib3", "2.9.0")

    def test_locked_version(self):
        assert locked_version(_LOCK, "urllib3") == "2.8.0"
        assert locked_version(_LOCK, "requests") is None

    def test_cooldown_cutoff_span(self):
        now = datetime(2026, 10, 29, tzinfo=UTC)
        assert cooldown_cutoff(_LOCK, now) == datetime(2026, 10, 22, tzinfo=UTC)

    def test_cooldown_cutoff_timestamp(self):
        lock = '[options]\nexclude-newer = "2026-08-01T00:00:00Z"\n'
        assert cooldown_cutoff(lock, datetime.now(UTC)) == datetime(2026, 8, 1, tzinfo=UTC)

    def test_no_cooldown(self):
        assert cooldown_cutoff("version = 1\n", datetime.now(UTC)) is None


_PYPROJECT = '[project]\nname = "x"\n\n[tool.uv]\nexclude-newer = "7 days"\n'


class TestExemptions:
    def test_add_to_pyproject(self):
        out = add_exemptions(_PYPROJECT, {"urllib3": "2026-09-15T19:29:37Z"}, uv_toml=False)
        assert out == (
            _PYPROJECT + f'\n[tool.uv.exclude-newer-package]\nurllib3 = "2026-09-15T19:29:37Z" # {EXEMPTION_MARKER}\n'
        )

    def test_add_to_uv_toml(self):
        out = add_exemptions('exclude-newer = "7 days"\n', {"urllib3": "2026-09-15T19:29:37Z"}, uv_toml=True)
        assert out.endswith(f'[exclude-newer-package]\nurllib3 = "2026-09-15T19:29:37Z" # {EXEMPTION_MARKER}\n')

    def test_add_keeps_inline_entries(self):
        config = 'exclude-newer = "7 days"\nexclude-newer-package = { setuptools = false }\n'
        out = add_exemptions(config, {"urllib3": "2026-09-15T19:29:37Z"}, uv_toml=True)
        assert "setuptools = false" in out
        assert f'urllib3 = "2026-09-15T19:29:37Z" # {EXEMPTION_MARKER}' in out

    def test_prune_expired(self):
        config = add_exemptions(_PYPROJECT, {"urllib3": "2026-09-15T19:29:37Z"}, uv_toml=False)
        assert prune_exemptions(config, datetime(2026, 10, 1, tzinfo=UTC), uv_toml=False) == _PYPROJECT

    def test_prune_keeps_live(self):
        config = add_exemptions(_PYPROJECT, {"urllib3": "2026-09-15T19:29:37Z"}, uv_toml=False)
        assert prune_exemptions(config, datetime(2026, 9, 1, tzinfo=UTC), uv_toml=False) == config

    def test_prune_keeps_user_entries(self):
        config = _PYPROJECT + '\n[tool.uv.exclude-newer-package]\nfoo = "2020-01-01T00:00:00Z"\nbar = false\n'
        assert prune_exemptions(config, datetime(2026, 10, 1, tzinfo=UTC), uv_toml=False) == config

    def test_prune_without_cooldown(self):
        config = add_exemptions('[project]\nname = "x"\n', {"urllib3": "2026-09-15T19:29:37Z"}, uv_toml=False)
        assert "urllib3" not in prune_exemptions(config, None, uv_toml=False)

    def test_user_exemption(self):
        config = 'exclude-newer = "7 days"\nexclude-newer-package = { Urllib3 = false }\n'
        assert has_user_exemption(config, "urllib3", uv_toml=True)

    def test_own_exemption_is_not_users(self):
        config = add_exemptions(_PYPROJECT, {"urllib3": "2026-09-15T19:29:37Z"}, uv_toml=False)
        assert not has_user_exemption(config, "urllib3", uv_toml=False)
        assert not has_user_exemption(_PYPROJECT, "urllib3", uv_toml=False)
