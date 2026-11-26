---
name: dagger
description: Run, set up, and write Dagger 1.0 checks, generators, services, and modules. Use when the repository has dagger.toml, dagger.json, or dagger-module.toml, or when the user mentions Dagger, dag:// links, or Dagger Cloud.
---

# Dagger

Dagger is CI. It runs the same checks before push (`dagger check`) and after
push (Cloud Checks). Checks run as functions in containers. Modules run native
tools, such as `go test` or ESLint, and read their native configuration, such as
`go.mod`.

## When to use it

- `dagger.toml` exists: prefer `dagger` to native tools to build, test, lint,
  and generate. A native command, such as `go test ./pkg`, is fine for a quick
  loop, but verify each change with `dagger check`.
- Only `dagger.json` exists: it is Dagger 0.x configuration. Dagger 1.0 runs it.
  Migrate only when the user asks.
- `dagger-module.toml` exists, with or without `dagger.toml`: the repository is
  a module. Read [write-a-module.md](write-a-module.md).
- No file exists: set up Dagger only when the user asks.

## Read first

Dagger 1.0 is different from 0.x. Do not trust your memory.

- The first time you do a task in "Tasks", read its docs page. Read each page
  once per session. Do not skip this for setup.
- To find other pages, read https://docs.dagger.io/llms.txt. Do not use web
  search results or blog posts.
- For toolchain guides, such as Go or ESLint, and CI migration guides, read the
  "Guides" list of https://dagger.io/llms.txt. Add `.md` to a guide URL to get
  Markdown. Pages on docs.dagger.io have no `.md` form.
- For commands and flags, read `dagger COMMAND --help`. If the docs and the CLI
  disagree, follow the CLI.

## Terms

- **Workspace**: a directory with `dagger.toml`, usually a Git repository.
  `dagger.toml` lists the installed modules and their settings.
- **Module**: a package of functions. Its manifest is `dagger-module.toml`.
- **Setting**: a value for a module constructor argument.
- **Check**: a function that passes or fails, such as a test or a linter.
- **Generator**: a function that returns a changeset (proposed file changes).
- **Collection**: a set of artifacts (typed objects, such as Go packages or test
  files) that a module finds at run time. Each collection adds a flag to select
  artifacts.
- **DAG link**: the `dag://` address of a check, generator, service, or
  artifact. To list links, add `-l -a -f link` to `dagger check`, `generate`, or
  `start`. For the syntax, read `dagger help links`. A `dag+check://` or
  `dag+generator://` link is the same address as its `dag://` form.

## Tasks

| Task | Start with | Docs (docs.dagger.io) |
| --- | --- | --- |
| Set up a workspace | `dagger init` | /getting-started/quickstart |
| Add a module for a tool | `dagger module recommend`, `dagger module search TOOL` | /reference/modules |
| Give project tools or services to a module | Read [write-a-module.md](write-a-module.md) | /config/module-wiring |
| Change module settings | `dagger module settings` | /config |
| Run checks | `dagger check -l` | /using/checking |
| Run generators | `dagger generate -l` | /using/generating |
| Start services | `dagger start -l` | /using/services |
| Call a function | `dagger api functions` | /using/calling-functions |
| Run checks after each push to GitHub | `dagger cloud checks on` | /cloud-checks |
| Move from another CI | Read the guide for that CI | /cloud-checks |
| Write a module | Read [write-a-module.md](write-a-module.md) | /reference/sdks |
| Migrate from 0.x | `dagger workspace migrate` | /migrate-to-v1 |

## Finish setup

Setup is done when at least one check of the project's real code passes.

1. Run `dagger check -l`. Each toolchain module, such as `dagger.io/go`, must
   list checks for the project's code. If a module lists no checks or misses code, first look for
   missing native configuration, such as no `go.mod` at the expected path.
   Then read the module page for its settings.
2. Run `dagger check`. Report each failure and each skipped test.
3. Find agent and contributor instructions that name native commands, such as
   `./check.sh` or `go test` in `AGENTS.md`, `CLAUDE.md`, or `CONTRIBUTING.md`.
   Tell the user, and offer to replace them with the matching `dagger`
   commands.
4. Tell the user which modules you installed and which commands run them.

## Move from another CI

Moving CI is a separate task from setup. It is done when Dagger checks cover
what each old job tests.

- Keep the old jobs. Remove a job only after Dagger checks cover what it tests
  and give the same results.
- Checks run in Linux containers. Keep jobs that need another platform, such as
  Windows or macOS runners. Tell the user which jobs stay.
- Cloud Checks run checks only. Put each deploy or release step in a function
  of the dev module, not in a check. The existing CI or the user calls it with
  `dagger api call`.

## Command rules

- To apply a changeset, add `-y`. To preview it, add `--no-apply`.
- Ask the user before a `dagger cloud` command that changes Cloud configuration.
  If a step needs a browser, such as login, tell the user to do it.
- Do not use `dagger agent`, `-i`, or `dagger shell` without `-c`. They need an
  interactive terminal. `dagger shell FUNCTION -c 'COMMAND'` runs COMMAND in the
  container that FUNCTION returns.
- Run `dagger check` and `dagger start` in the background. A full check can
  run longer than a command timeout, and `dagger start` does not exit. Give
  `dagger start` a service name or a collection flag.
- If a setting value starts with `-`, put `--` before it, such as
  `dagger module settings go goflags -- -tags=extended`.
- If the engine does not start on the local machine, report the error. Do not
  change `DAGGER_ENGINE` unless the user asks.
- Pass secrets as URIs, such as `env://TOKEN`. Do not put secret values in files
  or commands. Put other values in native configuration or settings, not in host
  environment variables. Host environment variables do not reach checks. To
  give a test an environment variable, set it in the container.
- Do not add logic that skips checks for unchanged files. The cache skips repeat
  work.
- Commit `dagger.toml`, `dagger.lock`, and the module directories, such as
  `.dagger`.

## Verify a change

1. At the start of the task, run `dagger check` once. Report failures. Fix
   them only when the user asks.
2. After the change, run the affected checks. Select them with a DAG link or a
   collection flag, such as `--go-package=PATH`.
3. Run `dagger check` again. Fix new failures until no new check fails.

A skipped test does not fail its check. If the report shows skipped tests, find
the cause: a missing executable, build tag, environment setting, or service, or
a test that needs another platform. Fix it with a module setting or a dev
module. Read [write-a-module.md](write-a-module.md).

## Fix a failure

- If you are logged in to Dagger Cloud, read the failure with
  `dagger cloud traces view --last` or `dagger cloud traces view URL`.
- Check link ends with `/stale`, such as `dag://?check=MODULE/NAME/stale`: run
  `dagger generate -y 'dag://?generator=MODULE/NAME'`. Do not edit generated
  files.
- File not found: the check sees only the files that its module includes, not
  the whole workspace. To include the file, read the toolchain guide and the
  module page.
- Other check failure: look for a function that fixes it, such as a formatter
  or a generator, with `dagger api functions MODULE`. Or read the module page
  under https://docs.dagger.io/reference/modules.
- Run the failed check again by its DAG link.
- Command fails without a clear cause: compare `dagger version` with
  https://docs.dagger.io/getting-started/install. Tell the user if the CLI is
  older. Suggest `dagger lock update` if `dagger.lock` pins old versions.
