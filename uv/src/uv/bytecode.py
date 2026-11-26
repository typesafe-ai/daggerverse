"""Bytecode compilation settings, shared by `builder` and `pip_venv`."""

from dagger_clients.core import Container


def with_compiled_bytecode(ctr: Container) -> Container:
    """`ctr`, set to compile what uv installs to unchecked hash-based `.pyc` files (`compile_bytecode`)."""
    return ctr.with_env_variable("UV_COMPILE_BYTECODE", "1").with_env_variable(
        "PYC_INVALIDATION_MODE", "UNCHECKED_HASH"
    )
