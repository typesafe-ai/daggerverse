"""Backend settings determine scaffold paths and the files copied for installation."""

import pytest
from uv.workspace.uv_build import UvBuildLayout


@pytest.mark.parametrize("root", ["", ".", "./"])
def test_flat_roots_copy_only_declared_modules(root):
    layout = UvBuildLayout.from_pyproject(
        {
            "build-system": {"build-backend": "uv_build"},
            "tool": {
                "uv": {"build-backend": {"module-name": ["actual_package", "compat_package"], "module-root": root}}
            },
        },
        "renamed-dist",
    )
    assert layout is not None
    assert layout.module_root == ""
    assert layout.module_paths == layout.source_paths == ["actual_package", "compat_package"]


def test_default_module_name_and_source_root():
    layout = UvBuildLayout.from_pyproject({"build-system": {"build-backend": "uv_build"}}, "Example.Dist-Name")
    assert layout is not None
    assert layout.module_paths == ["src/example_dist_name"]
    assert layout.source_paths == ["src"]


def test_dotted_module_under_custom_root():
    layout = UvBuildLayout.from_pyproject(
        {
            "build-system": {"build-backend": "uv_build"},
            "tool": {"uv": {"build-backend": {"module-name": "example.api", "module-root": "./python"}}},
        },
        "example-dist",
    )
    assert layout is not None
    assert layout.module_paths == ["python/example/api"]
    assert layout.source_paths == ["python"]


@pytest.mark.parametrize("backend", ["hatchling.build", "setuptools.build_meta"])
def test_other_backends_use_the_generic_layout(backend):
    assert UvBuildLayout.from_pyproject({"build-system": {"build-backend": backend}}, "example") is None
