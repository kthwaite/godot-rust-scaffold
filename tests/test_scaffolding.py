import configparser
import io
import json
import subprocess
import tomllib
from pathlib import Path
from unittest.mock import patch

import pytest
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from typer.testing import CliRunner

from godot_rust_scaffold.cli import Renderer, app, create_project


@pytest.fixture(autouse=True)
def registry_response():
    releases = [
        {
            "vers": version,
            "yanked": False,
            "features": {"api-4-2": [], "api-4-6": [], "api-4-6-2": []},
            "features2": {"api-custom": [], "double-precision": []},
        }
        for version in ("0.4.5", "0.5.5")
    ]
    data = "\n".join(json.dumps(release) for release in releases).encode()
    with patch(
        "godot_rust_scaffold.cli.urlopen", side_effect=lambda *a, **kw: io.BytesIO(data)
    ) as fetch:
        yield fetch


@pytest.fixture(autouse=True)
def external_tools():
    # Keep generation real, but do not require Git, Cargo, Godot, or downloads.
    engine_output = 'Godot Engine\nGODOT_RUST_SCAFFOLD:{"version":"4.6.2","status":"stable","double":true}\n'
    with (
        patch("godot_rust_scaffold.cli.shutil.which", return_value="/tools/available"),
        patch(
            "godot_rust_scaffold.cli.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, engine_output, ""),
        ) as command,
    ):
        yield command


@pytest.fixture
def picker_input():
    # Exercise the actual picker key bindings without requiring a terminal.
    with create_pipe_input() as pipe:
        with create_app_session(input=pipe, output=DummyOutput()):
            yield pipe


def invoke(parent, *options, name="My Game"):
    return CliRunner().invoke(app, [name, "--parent", str(parent), "--yes", *options])


def read_toml(path):
    return tomllib.loads(path.read_text(encoding="utf-8"))


def read_godot_config(path):
    config = configparser.ConfigParser(interpolation=None)
    # project.godot has a top-level config_version before its named sections.
    config.read_string("[root]\n" + path.read_text(encoding="utf-8"))
    return config


@pytest.mark.parametrize(
    ("renderer", "feature", "desktop", "mobile"),
    [
        ("forward+", "Forward Plus", "forward_plus", "mobile"),
        ("mobile", "Mobile", "mobile", "mobile"),
        ("compatibility", "GL Compatibility", "gl_compatibility", "gl_compatibility"),
    ],
)
def test_generated_project_preserves_title_and_renderer_settings(
    tmp_path, renderer, feature, desktop, mobile
):
    title = 'Café "Game" \\ Adventure'
    result = invoke(tmp_path, "--renderer", renderer, name=title)

    assert result.exit_code == 0, result.output
    root = tmp_path / "caf-game-adventure"
    project = read_godot_config(root / "godot/project.godot")
    assert json.loads(project["application"]["config/name"]) == title
    assert (
        project["application"]["config/features"]
        == f'PackedStringArray("4.2", "{feature}")'
    )
    assert json.loads(project["rendering"]["renderer/rendering_method"]) == desktop
    assert (
        json.loads(project["rendering"]["renderer/rendering_method.mobile"]) == mobile
    )
    scene = json.loads(project["application"]["run/main_scene"])
    assert (root / "godot" / scene.removeprefix("res://")).is_file()

    manifest = read_toml(root / "rust/Cargo.toml")
    assert manifest["package"]["name"] == "game_caf_game_adventure"
    assert manifest["lib"]["crate-type"] == ["cdylib"]
    assert manifest["dependencies"]["godot"] == {
        "version": "=0.5.5",
        "features": ["api-4-2"],
    }
    config = read_toml(root / "rust/.cargo/config.toml")
    assert config["build"]["target-dir"] == "target"
    assert "env" not in config

    extension = read_godot_config(root / "godot/game.gdextension")
    assert json.loads(extension["configuration"]["compatibility_minimum"]) == "4.2"
    for platform, library in (
        ("linux", "libgame_caf_game_adventure.so"),
        ("windows", "game_caf_game_adventure.dll"),
        ("macos", "libgame_caf_game_adventure.dylib"),
    ):
        for profile in ("debug", "release"):
            for architecture in ("x86_64", "arm64"):
                key = f"{platform}.{profile}.{architecture}"
                assert (
                    json.loads(extension["libraries"][key])
                    == f"res://../rust/target/{profile}/{library}"
                )


@pytest.mark.parametrize(
    ("requested", "api", "minimum"),
    [("4.6.0", "api-4-6", "4.6"), ("4.6.2", "api-4-6-2", "4.6.2")],
)
def test_version_override_agrees_across_generated_configs(
    tmp_path, requested, api, minimum
):
    result = invoke(
        tmp_path, "--godot-crate-version", "0.4.5", "--godot-version", requested
    )

    assert result.exit_code == 0, result.output
    root = tmp_path / "my-game"
    dependency = read_toml(root / "rust/Cargo.toml")["dependencies"]["godot"]
    assert dependency == {"version": "=0.4.5", "features": [api]}
    project = read_godot_config(root / "godot/project.godot")
    assert (
        project["application"]["config/features"]
        == 'PackedStringArray("4.6", "GL Compatibility")'
    )
    extension = read_godot_config(root / "godot/game.gdextension")
    assert json.loads(extension["configuration"]["compatibility_minimum"]) == minimum


@pytest.mark.parametrize(
    ("crate_version", "variable"),
    [("0.4.5", "GODOT4_BIN"), ("0.5.5", "GDRUST_GODOT_BIN")],
)
def test_double_precision_persists_custom_api_and_escaped_engine_path(
    tmp_path, monkeypatch, crate_version, variable
):
    monkeypatch.chdir(tmp_path)
    engine = Path('custom "engine" \\ bin/godot')
    result = invoke(
        tmp_path,
        "--godot-crate-version",
        crate_version,
        "--godot-version",
        "4.6.2",
        "--double-precision",
        "--godot-bin",
        str(engine),
    )

    assert result.exit_code == 0, result.output
    root = tmp_path / "my-game"
    dependency = read_toml(root / "rust/Cargo.toml")["dependencies"]["godot"]
    assert dependency == {
        "version": f"={crate_version}",
        "features": ["api-custom", "double-precision"],
    }
    config = read_toml(root / "rust/.cargo/config.toml")
    assert config["env"] == {variable: {"value": str(engine.resolve()), "force": True}}
    extension = read_godot_config(root / "godot/game.gdextension")
    assert json.loads(extension["configuration"]["compatibility_minimum"]) == "4.6.2"


@pytest.mark.parametrize("precision", ["single", "double"])
def test_interactive_selections_control_generated_project(
    tmp_path, precision, picker_input
):
    engine = tmp_path / "custom-godot"
    engine_input = f"{engine}\n" if precision == "double" else ""
    precision_keys = "\x1b[B" if precision == "double" else ""
    picker_input.send_text(f"\x1b[A\r{precision_keys}\r\x1b[B\x1b[B\r")
    result = CliRunner().invoke(
        app,
        [],
        input=f"  Interactive Game  \n{tmp_path}\n{engine_input}y\n",
    )

    assert result.exit_code == 0, result.output
    root = tmp_path / "interactive-game"
    project = read_godot_config(root / "godot/project.godot")
    assert json.loads(project["application"]["config/name"]) == "Interactive Game"
    assert json.loads(project["rendering"]["renderer/rendering_method"]) == "mobile"
    extension = read_godot_config(root / "godot/game.gdextension")
    assert json.loads(extension["configuration"]["compatibility_minimum"]) == "4.6.2"
    dependency = read_toml(root / "rust/Cargo.toml")["dependencies"]["godot"]
    assert dependency["features"] == (
        ["api-custom", "double-precision"] if precision == "double" else ["api-4-6-2"]
    )
    config = read_toml(root / "rust/.cargo/config.toml")
    if precision == "double":
        assert config["env"]["GDRUST_GODOT_BIN"]["value"] == str(engine)
    else:
        assert "env" not in config


def test_interactive_defaults_create_standard_compatibility_project(
    tmp_path, picker_input
):
    picker_input.send_text("\r\r\r")
    result = CliRunner().invoke(
        app, [], input=f"Default Game\n{tmp_path}\n\n"
    )

    assert result.exit_code == 0, result.output
    root = tmp_path / "default-game"
    project = read_godot_config(root / "godot/project.godot")
    assert (
        json.loads(project["rendering"]["renderer/rendering_method"])
        == "gl_compatibility"
    )
    dependency = read_toml(root / "rust/Cargo.toml")["dependencies"]["godot"]
    assert dependency["features"] == ["api-4-2"]


def test_explicit_settings_skip_interactive_selection(tmp_path):
    result = CliRunner().invoke(
        app,
        [
            "Explicit Game", "--parent", str(tmp_path),
            "--renderer", "compatibility", "--godot-version", "4.6.2",
            "--double-precision", "--godot-bin", str(tmp_path / "engine"),
        ],
        input="y\n",
    )

    assert result.exit_code == 0, result.output
    dependency = read_toml(
        tmp_path / "explicit-game/rust/Cargo.toml"
    )["dependencies"]["godot"]
    assert dependency["features"] == ["api-custom", "double-precision"]


def test_interactive_api_choices_follow_selected_crate(
    tmp_path, registry_response, picker_input
):
    release = {
        "vers": "0.4.5",
        "yanked": False,
        "features": {"api-4-1": [], "api-4-10": []},
        "features2": {"api-4-6": []},
    }
    registry_response.side_effect = lambda *a, **kw: io.BytesIO(
        json.dumps(release).encode()
    )
    # Enter selects the lowest supported API, not an unsupported default or 4.10.
    picker_input.send_text("\r\r\r")
    result = CliRunner().invoke(
        app,
        ["My Game", "--parent", str(tmp_path), "--godot-crate-version", "0.4.5"],
        input="y\n",
    )

    assert result.exit_code == 0, result.output
    dependency = read_toml(tmp_path / "my-game/rust/Cargo.toml")["dependencies"]["godot"]
    assert dependency == {"version": "=0.4.5", "features": ["api-4-6"]}


def test_declining_confirmation_creates_nothing(tmp_path, external_tools, picker_input):
    picker_input.send_text("\r\r\r")
    result = CliRunner().invoke(
        app, ["My Game", "--parent", str(tmp_path)], input="n\n"
    )

    assert result.exit_code == 0
    assert not (tmp_path / "my-game").exists()
    external_tools.assert_not_called()


@pytest.mark.parametrize("input_text", ["", "My Game\n"])
def test_end_of_input_aborts_without_creating_files(tmp_path, monkeypatch, input_text):
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(app, ["--renderer", "mobile"], input=input_text)

    assert result.exit_code == 1
    assert not (tmp_path / "my-game").exists()


@pytest.mark.parametrize(
    "options", [["--yes"], ["My Game", "--yes"], ["--yes", "--parent", "."]]
)
def test_noninteractive_mode_requires_name_and_parent(
    tmp_path, monkeypatch, registry_response, options
):
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(app, options)

    assert result.exit_code == 1
    assert not (tmp_path / "my-game").exists()
    registry_response.assert_not_called()


@pytest.mark.parametrize("name", ["", "   ", "bad\nname", "!!!", "日本語"])
def test_invalid_name_is_rejected_before_network_or_creation(
    tmp_path, registry_response, name
):
    result = invoke(tmp_path, name=name)

    assert result.exit_code == 1
    assert list(tmp_path.iterdir()) == []
    registry_response.assert_not_called()


def test_missing_parent_is_not_created(tmp_path, registry_response):
    parent = tmp_path / "absent"
    result = invoke(parent)

    assert result.exit_code == 1
    assert not parent.exists()
    registry_response.assert_not_called()


@pytest.mark.parametrize("kind", ["directory", "file", "symlink", "dangling-symlink"])
def test_existing_destination_is_never_overwritten(tmp_path, registry_response, kind):
    destination = tmp_path / "my-game"
    if kind == "directory":
        destination.mkdir()
        original = destination / "keep.txt"
    elif kind == "file":
        original = destination
    else:
        original = tmp_path / "target"
        destination.symlink_to(original)
    if kind != "dangling-symlink":
        original.write_text("user data", encoding="utf-8")

    result = invoke(tmp_path)

    assert result.exit_code == 1
    registry_response.assert_not_called()
    if kind == "dangling-symlink":
        assert destination.is_symlink()
        assert not original.exists()
    else:
        assert original.read_text(encoding="utf-8") == "user data"
    if kind in ("symlink", "dangling-symlink"):
        assert destination.readlink() == original


@pytest.mark.parametrize(
    ("options", "exit_code"),
    [(["--godot-bin", "/custom/godot"], 1), (["--renderer", "unknown"], 2)],
)
def test_invalid_options_rejected_before_network(
    tmp_path, registry_response, options, exit_code
):
    result = invoke(tmp_path, *options)

    assert result.exit_code == exit_code
    assert not (tmp_path / "my-game").exists()
    registry_response.assert_not_called()


@pytest.mark.parametrize("missing", ["git", "cargo"])
def test_missing_tool_leaves_destination_untouched(tmp_path, external_tools, missing):
    with patch(
        "godot_rust_scaffold.cli.shutil.which",
        side_effect=lambda tool: None if tool == missing else "/tools/available",
    ):
        result = invoke(tmp_path)

    assert result.exit_code == 1
    assert not (tmp_path / "my-game").exists()
    external_tools.assert_not_called()


def test_project_api_rejects_missing_parent(tmp_path, external_tools):
    parent = tmp_path / "absent"
    with pytest.raises(ValueError):
        create_project(
            "My Game",
            parent,
            crate_version="0.5.5",
            godot_version="4.2",
            double_precision=False,
            godot_bin=None,
            renderer=Renderer.COMPATIBILITY,
        )

    assert not parent.exists()
    external_tools.assert_not_called()


@pytest.mark.parametrize(
    ("error", "exit_code"),
    [
        (subprocess.CalledProcessError(101, ["cargo", "build"]), 1),
        (OSError("disk unavailable"), 1),
        (KeyboardInterrupt(), 130),
    ],
)
def test_build_failure_preserves_generated_project_for_recovery(
    tmp_path, external_tools, error, exit_code
):
    def command(args, **kwargs):
        if args[:2] == ["cargo", "build"]:
            raise error
        return subprocess.CompletedProcess(args, 0)

    external_tools.side_effect = command
    result = invoke(tmp_path)

    assert result.exit_code == exit_code
    root = tmp_path / "my-game"
    assert read_toml(root / "rust/Cargo.toml")["package"]["name"] == "game_my_game"
    assert (root / "godot/main.tscn").is_file()
    assert (root / "rust/src/lib.rs").is_file()

    # A retry must not overwrite the partial project or user repairs.
    repair = root / "rust/src/lib.rs"
    repair.write_text("user repair", encoding="utf-8")
    retry = invoke(tmp_path)
    assert retry.exit_code == 1
    assert repair.read_text(encoding="utf-8") == "user repair"
