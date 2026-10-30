# zizmor

Dagger module for [zizmor](https://github.com/zizmorcore/zizmor), a static analyzer for GitHub Actions security issues.

It exposes one generator, `fix` (`zizmor --fix`), over the workspace's `.github` directory.
`dagger generate` applies its fixes, and `dagger check --generated` fails while it would
change a file. `fix` also fails on findings zizmor can't fix.

## Installation

```sh
dagger install github.com/typesafe-ai/daggerverse/zizmor
```

## Usage

Fail when workflows have findings:

```sh
dagger check --zizmor --generated
```

Apply the fixes to the workspace:

```sh
dagger generate --zizmor
```

## Configuration

Generators run with their default arguments, so options are constructor settings in `dagger.toml`.
String and enum settings are JSON, so they need inner quotes:

```toml
[modules.zizmor.settings]
fixMode = '"ALL"'
persona = '"pedantic"'
```
