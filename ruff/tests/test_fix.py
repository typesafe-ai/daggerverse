import asyncio

import dagger
import pytest
import ruff.checker as checker_module
import ruff.formatter as formatter_module
from ruff.checker import RuffChecker
from ruff.formatter import RuffFormatter


class FakeDirectory:
    def with_directory(self, *args, **kwargs):
        return self

    def without_directory(self, *args, **kwargs):
        return self

    def changes(self, before):
        return "changeset"


class RuffExecutionError(RuntimeError):
    pass


class FakeExecResult:
    def __init__(self, exit_code, expect):
        self.exit_code = exit_code
        self.expect = expect

    async def combined_output(self):
        if self.exit_code != 0 and self.expect is dagger.ReturnType.SUCCESS:
            raise RuffExecutionError(f"ruff exited with exit code: {self.exit_code}")
        return "ruff output\n"

    def directory(self, path):
        return FakeDirectory()


class FakeContainer:
    def __init__(self, exit_code):
        self.exit_code = exit_code
        self.args: list[str] | None = None

    def with_workdir(self, path):
        return self

    def with_mounted_directory(self, path, source):
        return self

    def with_exec(self, args, *, expect=dagger.ReturnType.SUCCESS):
        self.args = args
        return FakeExecResult(self.exit_code, expect)


@pytest.mark.parametrize(
    ("module", "subject"),
    [
        (checker_module, RuffChecker),
        (formatter_module, RuffFormatter),
    ],
)
def test_fix_raises_when_ruff_exits_2(monkeypatch, module, subject):
    monkeypatch.setattr(module.dag, "directory", FakeDirectory)
    ruff = subject(ctr=FakeContainer(exit_code=2))

    with pytest.raises(RuffExecutionError, match="exit code: 2"):
        asyncio.run(ruff.fix(source=FakeDirectory()))


@pytest.mark.parametrize(
    ("module", "subject"),
    [
        (checker_module, RuffChecker),
        (formatter_module, RuffFormatter),
    ],
)
def test_fix_returns_changeset_when_ruff_succeeds(monkeypatch, module, subject):
    monkeypatch.setattr(module.dag, "directory", FakeDirectory)
    container = FakeContainer(exit_code=0)
    ruff = subject(ctr=container)

    assert asyncio.run(ruff.fix(source=FakeDirectory())) == "changeset"
    assert container.args is not None
    assert ("--exit-zero" in container.args) is (subject is RuffChecker)
