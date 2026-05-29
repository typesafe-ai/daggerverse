"""Tests for actionlint helpers (pure logic, no Dagger runtime)."""

from actionlint.main import release_url


class TestReleaseUrl:
    def test_amd64(self):
        assert release_url("1.7.12", "amd64") == (
            "https://github.com/rhysd/actionlint/releases/download/"
            "v1.7.12/actionlint_1.7.12_linux_amd64.tar.gz"
        )

    def test_arm64(self):
        url = release_url("1.7.12", "arm64")
        assert url.endswith("actionlint_1.7.12_linux_arm64.tar.gz")
        assert "/v1.7.12/" in url
