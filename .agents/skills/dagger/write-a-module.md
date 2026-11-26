# Write a Dagger module

First, do "Add a module for a tool" in SKILL.md. Use an official module if one
fits. Write a module only in these cases:

- **Dev module:** an official module fits, but the project needs more: a tool,
  such as `protoc`, a service, such as a database, a project step that no module
  covers, or a deploy step.
- **Toolchain module:** no module fits the toolchain. Write a reusable module.
  Do not put project names, paths, or values in it.

Use Dang, the Dagger language. Install it with
`dagger module install dagger.io/sdk/dang`. Use another SDK only for code that
needs a library from another language, such as a parser. Keep that code in a
small helper that the Dang module calls. Read the Dang page under
https://docs.dagger.io/reference/sdks.

## Dev module

1. Run `dagger module init dang -y`. This creates `.dagger/modules/PROJECT-dev`
   and makes it the workspace main module, so commands accept its functions
   without the module name. Keep the type name that `init` writes.
2. Write functions only for what the official modules cannot know: a container
   with the project tools, a `Service`, a check for a project step, or a deploy
   function. Do not copy the logic of an official module. Test a container with
   `dagger shell FUNCTION -c 'TOOL --version'`.
3. Wire the container or service into a setting of the official module, such as
   `dagger module settings go base test-runtime`. Read the module page for the
   setting names. The value `test-runtime` names a dev module function that
   takes no arguments and returns the setting's type. The command writes it as
   `dag://PROJECT-dev/test-runtime`. In `dagger.toml` and in CLI flags, write
   the `dag://` form: a bare name there is an image reference or a path. Take
   each tool version from native configuration. If only
   the old CI pins a version, such as a GitHub Actions workflow, use that
   version and tell the user that both places must change together.
4. For a test service, bind it in the container with `withServiceBinding`. Give
   its address in an environment variable that the tests read. Run the check
   again. The skipped tests must now run.

## Toolchain module

A toolchain module is a plugin that adds Dagger support for a toolchain, open
source or in-house. Create it with `dagger module init dang --name NAME -y`.
Read the Collections example on the Dang page. For design patterns, read
`DANG_MODULE_DEVELOPER_MANUAL.md` in https://github.com/dagger/go.

1. **Native configuration first.** Read each value from native configuration,
   such as the Go version from `go.mod`. Do not copy values into settings,
   because copies become incorrect. If the toolchain has no place for a value,
   add one in the native style. For example, `dagger.io/go` reads
   `//go:test:include` comments to find test files.
2. **Artifacts first.** Give a type to each artifact of the toolchain: source
   packages, configuration scopes, generated files, binaries, containers, and
   test results. Some are files in the workspace. The module derives the
   others. Put the fields and functions of each artifact on its type.
3. **Collections.** Return each set of artifacts as a collection. The flag name
   is the module name plus the artifact type name, such as `--go-package`. Use
   short type names. If one tool run can handle many artifacts, add a batch
   check to the collection.
4. **Checks and generators.** Mark them, so that `dagger check` and
   `dagger generate` find them. A check takes no arguments from the caller.
   Dagger fills the arguments it provides, such as `ws: Workspace!`.
5. **Settings.** Put values about the module itself, such as a base image, in
   constructor arguments. Give each one a default, so that the checks run with
   no settings.
6. **Workspace files.** Give a `ws: Workspace!` argument to each function that
   reads files. Dagger fills it. Find the files from native configuration, such
   as `//go:embed` lines. Use narrow `include` filters, and exclude build
   output, such as `node_modules`. Each file adds upload time and can invalidate
   the cache.
7. **Services.** For a test dependency, such as a database, use a Dagger
   service. Do not use Docker in Docker.
8. **Dependencies.** To call another module, add it with
   `dagger module client add`, then call it by its dependency name. Do not
   rely on a module that the workspace installs but the module does not add.
9. **Tests.** Add fixtures that pass and fixtures that fail.

## After each change

Run the checks that call the changed functions. A function can load with no
errors and fail only when it runs. Then do "Verify a change" in SKILL.md.
