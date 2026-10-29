"""flit_core: the import name, metadata read from the module, and external data.

See https://flit.pypa.io/en/stable/pyproject_toml.html.
"""

from dataclasses import dataclass
from typing import Self

from uv.workspace.build_backends.paths import tree

# `[project].dynamic` fields flit reads from the module itself.
_MODULE_METADATA = {"version", "description"}


@dataclass(frozen=True)
class FlitSettings:
    """`[tool.flit]` settings that determine the import name and build inputs.

    flit finds the module under `src/` or at the package root, so the location is
    left to the generic layout; only the name comes from the settings.
    """

    # `[tool.flit.module].name`, dotted for a module inside namespace packages.
    module_name: str
    # The version or description is dynamic, so flit reads the module's docstring and `__version__`.
    reads_module: bool = False
    # `[tool.flit.external-data].directory`, shipped in the wheel's data.
    external_data: str | None = None

    @classmethod
    def from_pyproject(cls, metadata: dict, name: str) -> Self:
        flit = metadata.get("tool", {}).get("flit", {})
        dynamic = set(metadata.get("project", {}).get("dynamic", []))
        return cls(
            module_name=flit.get("module", {}).get("name", name.replace("-", "_")),
            reads_module=bool(dynamic & _MODULE_METADATA),
            external_data=flit.get("external-data", {}).get("directory"),
        )

    @property
    def module_paths(self) -> None:
        return None

    @property
    def source_paths(self) -> None:
        return None

    @property
    def build_inputs(self) -> list[str]:
        inputs = []
        if self.reads_module:
            # The package sits at the root or under `src/`; only the one that exists is staged.
            module = self.module_name.replace(".", "/")
            inputs += [f"{module}/__init__.py", f"src/{module}/__init__.py"]
        if self.external_data:
            inputs += tree(self.external_data)
        return inputs
