import pytest
from pyproject.discovery import SOURCE_EXCLUDE, Discovery, Manifest, matches, topmost

PROJECT = '[project]\nname = "{name}"\nversion = "0"\ndependencies = {deps!r}\n'


def project(name: str, extra: str = "", deps: list[str] | None = None) -> str:
    return PROJECT.format(name=name, deps=deps or []).replace("'", '"') + extra


def uv_workspace(name: str, members: list[str], exclude: list[str] | None = None, extra: str = "") -> str:
    text = project(name) + f"\n[tool.uv.workspace]\nmembers = {members!r}\n"
    if exclude:
        text += f"exclude = {exclude!r}\n"
    return text + extra


class TestHelpers:
    @pytest.mark.parametrize(
        ("path", "patterns", "expected"),
        [
            ("uv/tests/_packages/clean", ["**/tests/_packages/**"], True),
            ("uv/tests/_packages", ["**/tests/_packages/**"], True),
            ("uv/tests", ["**/tests/_packages/**"], False),
            ("packages/a", ["packages/*"], True),
            ("packages/a/b", ["packages/*"], False),
            ("packages/a", ["./packages/*/"], True),
        ],
    )
    def test_matches(self, path, patterns, expected):
        assert matches(path, patterns) is expected

    def test_topmost(self):
        assert topmost(["a/b", "a", "c/d", "c/d/e", "a"]) == ["a", "c/d"]


class TestManifest:
    def test_sources_resolve_against_the_declaring_project(self):
        manifest = Manifest.parse(
            "apps/web",
            project(
                "web", '\n[tool.uv.sources]\nMy_Lib = { path = "../../libs/my-lib" }\ncore = { workspace = true }\n'
            ),
        )
        assert manifest.sources["my-lib"].paths == ("libs/my-lib",)
        assert manifest.sources["core"].workspace

    def test_marker_split_sources(self):
        manifest = Manifest.parse(
            ".",
            project(
                "x",
                "\n[tool.uv.sources]\n"
                'torch = [{ path = "wheels/a.whl", marker = "sys_platform == \'linux\'" }, { index = "pytorch" }]\n',
            ),
        )
        assert manifest.sources["torch"].paths == ("wheels/a.whl",)
        assert not manifest.sources["torch"].workspace

    def test_dotted_keys(self):
        manifest = Manifest.parse(".", project("x", '\n[tool.uv]\nsources.lib = { path = "lib" }\n'))
        assert manifest.sources["lib"].paths == ("lib",)

    def test_declared_dependencies(self):
        manifest = Manifest.parse(
            ".",
            project(
                "x",
                "\n[project.optional-dependencies]\nviz = [\"Tabulate>=0.9; python_version>'3'\"]\n"
                '\n[dependency-groups]\ndev = ["pytest", { include-group = "lint" }]\nlint = ["ruff[all]"]\n'
                '\n[tool.uv]\ndev-dependencies = ["mypy==1.0"]\n',
                deps=["My_Lib @ file:///x"],
            ),
        )
        assert manifest.dependencies == {"my-lib", "tabulate", "pytest", "ruff", "mypy"}

    def test_dagger_generated(self):
        assert Manifest.parse(".", project("x", '\n[tool.dagger]\ngenerated = "core"\n')).generated
        assert not Manifest.parse(".", project("x", "\n[tool.dagger]\nother = 1\n")).generated
        assert not Manifest.parse(".", project("x")).generated

    def test_invalid_toml_names_the_file(self):
        with pytest.raises(ValueError, match=r"^a/pyproject.toml: "):
            Manifest.parse("a", "[project\n")


class TestDiscovery:
    def test_standalone_project(self):
        found = Discovery({"app": project("app")}, {"app": ["uv.lock"]})
        app = found.project("app")
        assert app.workspace == "app"
        assert app.local_dependencies == ()
        assert app.include == ("app", "app/**")
        assert app.exclude == tuple(SOURCE_EXCLUDE)
        workspace = found.workspace("app")
        assert workspace.members == ("app",)
        assert workspace.manifest_include == (
            "app/uv.lock",
            "app/uv.toml",
            "app/pyproject.toml",
            "app/.python-version",
        )

    def test_uv_workspace_members(self):
        found = Discovery(
            {
                "ws": uv_workspace("root", ["packages/*", "tools/cli"], exclude=["packages/skip"]),
                "ws/packages/a": project("a"),
                "ws/packages/skip": project("skip"),
                "ws/packages/a/nested": project("nested"),
                "ws/tools/cli": project("cli"),
                "ws/tools/other": project("other"),
            },
            {"ws": ["uv.lock"]},
        )
        assert found.workspace("ws").members == ("ws", "ws/packages/a", "ws/tools/cli")
        assert found.project("ws/packages/a").workspace == "ws"
        assert found.project("ws/packages/skip").workspace is None
        assert found.project("ws/tools/other").workspace is None
        # Not a member, and no source points at them: left out of the load.
        assert found.workspace("ws").exclude[len(SOURCE_EXCLUDE) :] == (
            "ws/packages/a/nested",
            "ws/packages/a/nested/**",
            "ws/packages/skip",
            "ws/packages/skip/**",
            "ws/tools/other",
            "ws/tools/other/**",
        )

    def test_workspace_without_uv_workspace_table_has_only_its_root(self):
        found = Discovery({".": project("root"), "sub": project("sub")}, {".": ["pylock.toml"]})
        workspace = found.workspace(".")
        assert workspace.members == (".",)
        assert workspace.lockfiles == ("pylock.toml",)
        assert workspace.manifest_include[0] == "pylock.toml"
        assert found.project("sub").workspace is None

    def test_bare_lockfile_has_no_members(self):
        found = Discovery({}, {"locks": ["pylock.toml"]})
        workspace = found.workspace("locks")
        assert workspace.members == ()
        assert workspace.include == ("locks", "locks/**")

    def test_path_dependency_outside_the_workspace_root(self):
        found = Discovery(
            {
                "nested/app": project(
                    "app", '\n[tool.uv.sources]\nlib = { path = "../my-lib", editable = true }\n', deps=["lib"]
                ),
                "nested/my-lib": project("lib"),
            },
            {"nested/app": ["uv.lock"]},
        )
        app = found.project("nested/app")
        assert app.local_dependencies == ("nested/my-lib",)
        assert app.include == ("nested/app", "nested/app/**", "nested/my-lib", "nested/my-lib/**")
        workspace = found.workspace("nested/app")
        assert workspace.local_dependencies == ("nested/my-lib",)
        assert "nested/my-lib/pyproject.toml" in workspace.manifest_include
        assert found.project("nested/my-lib").workspace is None

    def test_sources_for_undeclared_packages_are_ignored(self):
        # As in uv: a workspace root's sources name every member, but a member
        # only pulls in the ones it depends on.
        found = Discovery(
            {
                ".": uv_workspace(
                    "root",
                    ["libs/*"],
                    extra="\n[tool.uv.sources]\na = { workspace = true }\nb = { workspace = true }\n",
                ),
                "libs/a": project("a", deps=["b"]),
                "libs/b": project("b"),
                "tool": project("tool", '\n[tool.uv.sources]\na = { path = "../libs/a" }\n', deps=["a"]),
            },
            {".": ["uv.lock"], "tool": ["uv.lock"]},
        )
        assert found.project("libs/a").local_dependencies == ("libs/b",)
        assert found.project("libs/b").local_dependencies == ()
        assert found.project("tool").local_dependencies == ("libs/a", "libs/b")

    def test_transitive_path_dependencies(self):
        found = Discovery(
            {
                "app": project("app", '\n[tool.uv.sources]\nb = { path = "../b" }\n', deps=["b"]),
                "b": project("b", '\n[tool.uv.sources]\nc = { path = "../c" }\n', deps=["c"]),
                "c": project("c"),
            },
            {"app": ["uv.lock"]},
        )
        assert found.project("app").local_dependencies == ("b", "c")

    def test_dependency_cycle_terminates(self):
        found = Discovery(
            {
                "a": project("a", '\n[tool.uv.sources]\nb = { path = "../b" }\n', deps=["b"]),
                "b": project("b", '\n[tool.uv.sources]\na = { path = "../a" }\n', deps=["a"]),
            },
            {},
        )
        assert found.project("a").local_dependencies == ("b",)

    def test_missing_and_escaping_sources(self):
        found = Discovery(
            {
                "ws/sub": project(
                    "sub",
                    "\n[tool.uv.sources]\n"
                    'gone = { path = "../gone/pkg" }\n'
                    'outside = { path = "../../../outside" }\n'
                    'wheel = { path = "../wheels/w.whl" }\n',
                    deps=["gone", "outside", "wheel"],
                ),
            },
            {"ws/sub": ["uv.lock"]},
        )
        sub = found.project("ws/sub")
        # Neither a missing path nor a wheel is a project; nothing above the root can load.
        assert sub.local_dependencies == ()
        assert sub.include == (
            "ws/sub",
            "ws/sub/**",
            "ws/gone/pkg",
            "ws/gone/pkg/**",
            "ws/wheels/w.whl",
            "ws/wheels/w.whl/**",
        )
        assert "ws/wheels/w.whl" in found.workspace("ws/sub").manifest_include

    def test_workspace_sources_resolve_to_members_by_normalized_name(self):
        found = Discovery(
            {
                ".": uv_workspace("root", ["libs/*", "apps/*"]),
                "apps/web": project("web", "\n[tool.uv.sources]\nmy-lib = { workspace = true }\n", deps=["my-lib"]),
                "libs/my_lib": project("My.Lib"),
                "libs/unused": project("unused"),
            },
            {".": ["uv.lock"]},
        )
        web = found.project("apps/web")
        assert web.local_dependencies == ("libs/my_lib",)
        # Members other than the root and its dependencies stay out of a project's load.
        assert web.include == ("apps/web", "apps/web/**", "libs/my_lib", "libs/my_lib/**")

    def test_members_inherit_root_sources(self):
        found = Discovery(
            {
                ".": uv_workspace(
                    "root", ["apps/*"], extra='\n[tool.uv.sources]\nshared = { path = "vendor/shared" }\n'
                ),
                "apps/web": project("web", deps=["shared"]),
                "vendor/shared": project("shared"),
            },
            {".": ["uv.lock"]},
        )
        assert found.project("apps/web").local_dependencies == ("vendor/shared",)

    def test_member_sources_override_root_sources(self):
        found = Discovery(
            {
                ".": uv_workspace("root", ["apps/*"], extra='\n[tool.uv.sources]\nshared = { path = "vendor/a" }\n'),
                "apps/web": project(
                    "web", '\n[tool.uv.sources]\nshared = { path = "../../vendor/b" }\n', deps=["shared"]
                ),
                "vendor/a": project("a"),
                "vendor/b": project("b"),
            },
            {".": ["uv.lock"]},
        )
        assert found.project("apps/web").local_dependencies == ("vendor/b",)

    def test_root_project_leaves_out_unrelated_nested_projects(self):
        found = Discovery(
            {
                ".": project("root", '\n[tool.uv.sources]\nlib = { path = "lib" }\n', deps=["lib"]),
                "lib": project("lib"),
                "lib/tests/fixture": project("fixture"),
                "tools/a": project("a"),
                "tools/a/deep": project("deep"),
            },
            {".": ["uv.lock"]},
        )
        root = found.project(".")
        assert root.nested == ("lib", "tools/a")
        assert root.include == ("**", "lib", "lib/**")
        # Unrelated projects go, including ones nested in a needed project's tree.
        assert root.exclude[len(SOURCE_EXCLUDE) :] == (
            "lib/tests/fixture",
            "lib/tests/fixture/**",
            "tools/a",
            "tools/a/**",
        )

    def test_nested_workspaces_belong_to_the_deepest(self):
        found = Discovery(
            {
                ".": uv_workspace("outer", ["**"]),
                "inner": uv_workspace("inner", ["pkg"]),
                "inner/pkg": project("pkg"),
            },
            {".": ["uv.lock"], "inner": ["uv.lock"]},
        )
        assert found.project("inner/pkg").workspace == "inner"
        assert found.project(".").workspace == "."

    def test_several_markers(self):
        found = Discovery({"a": project("a")}, {"a": ["uv.lock", "pylock.toml"]})
        assert found.workspace("a").lockfiles == ("pylock.toml", "uv.lock")

    def test_invalid_manifest_only_fails_when_used(self):
        found = Discovery({"good": project("good"), "bad": "[project\n"}, {"good": ["uv.lock"]})
        assert found.project("good").name == "good"
        with pytest.raises(ValueError, match="bad/pyproject.toml"):
            found.project("bad")
