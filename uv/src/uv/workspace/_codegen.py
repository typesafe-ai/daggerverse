import posixpath
import tomllib

import dagger
from dagger import dag
from dagger.telemetry import get_tracer


def _needs_dagger_codegen(pyproject_contents: str) -> bool:
    """True when pyproject.toml declares dagger-io sourced from a local sdk directory."""
    pyproject = tomllib.loads(pyproject_contents)
    dagger_src = pyproject.get("tool", {}).get("uv", {}).get("sources", {}).get("dagger-io", {})
    return isinstance(dagger_src, dict) and dagger_src.get("path") == "sdk"


async def dagger_codegen(ws_dir: dagger.Directory, codegen_path: str) -> dagger.Directory:
    """Run Dagger codegen and overlay the generated SDK.

    No-op unless *both* ``dagger.json`` and a ``pyproject.toml`` with
    ``dagger-io`` sourced from a local ``sdk`` directory exist at
    *codegen_path*.

    Synthesizes a minimal directory (just ``dagger.json``) so codegen is
    cache-stable across edits to the user's module source.
    """
    tracer = get_tracer()

    with tracer.start_as_current_span("detect dagger module") as span:
        span.set_attribute("codegen.path", codegen_path)
        dagger_json_path = "dagger.json" if codegen_path == "." else posixpath.join(codegen_path, "dagger.json")
        if not await ws_dir.glob(dagger_json_path):
            span.set_attribute("codegen.skipped", True)
            span.set_attribute("codegen.skip_reason", "no dagger.json")
            return ws_dir
        pyproject_path = "pyproject.toml" if codegen_path == "." else posixpath.join(codegen_path, "pyproject.toml")
        pyproject_files = await ws_dir.glob(pyproject_path)
        if not pyproject_files or not _needs_dagger_codegen(await ws_dir.file(pyproject_path).contents()):
            span.set_attribute("codegen.skipped", True)
            span.set_attribute("codegen.skip_reason", "no local dagger-io source")
            return ws_dir
        dagger_json_contents = await ws_dir.file(dagger_json_path).contents()

    with tracer.start_as_current_span("run dagger codegen") as span:
        span.set_attribute("codegen.path", codegen_path)
        generated = (
            dag.directory()
            .with_new_file("dagger.json", dagger_json_contents)
            .as_module_source()
            .generated_context_directory()
        )
        sdk_overlay_path = "sdk" if codegen_path == "." else posixpath.join(codegen_path, "sdk")
        result = ws_dir.with_directory(sdk_overlay_path, generated.directory("sdk"))
        # Force evaluation inside the span: generated_context_directory() is lazy,
        # so without sync() the codegen runs later (on the caller's final sync) and
        # this span would capture only the glob/contents reads above, not the codegen.
        return await result.sync()
