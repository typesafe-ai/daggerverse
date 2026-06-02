import posixpath

import dagger
from dagger import dag
from dagger.telemetry import get_tracer


async def dagger_codegen(ws_dir: dagger.Directory, codegen_path: str) -> dagger.Directory:
    """Run Dagger codegen and overlay the generated SDK. No-op without `dagger.json`.

    Synthesizes a minimal directory (just `dagger.json`) so codegen is
    cache-stable across edits to the user's module source.
    """
    with get_tracer().start_as_current_span("dagger codegen") as span:
        span.set_attribute("codegen.path", codegen_path)
        dagger_json_path = "dagger.json" if codegen_path == "." else posixpath.join(codegen_path, "dagger.json")
        if not await ws_dir.glob(dagger_json_path):
            return ws_dir
        dagger_json_contents = await ws_dir.file(dagger_json_path).contents()
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
