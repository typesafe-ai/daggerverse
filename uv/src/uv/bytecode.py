"""Bytecode compilation settings, shared by `builder` and `pip_venv`."""

from dagger_clients.core import Container


async def with_compiled_bytecode(ctr: Container) -> Container:
    """`ctr`, set to compile what uv installs to unchecked hash-based `.pyc` files (`compile_bytecode`)."""
    ctr = ctr.with_env_variable("UV_COMPILE_BYTECODE", "1")
    if await ctr.env_variable("SOURCE_DATE_EPOCH"):
        return ctr
    # Hash-based .pyc files. 1980-01-01, not 0: the same epoch dates the wheels uv builds,
    # and ZIP can't store anything earlier.
    return ctr.with_env_variable("SOURCE_DATE_EPOCH", "315532800")
