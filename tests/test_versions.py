import io
import json
import subprocess
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

import pytest
from typer.testing import CliRunner

from godot_rust_scaffold.cli import app, validate_double_engine, validate_versions


def registry(*, yanked=False, features=None, features2=None):
    return io.BytesIO(
        json.dumps(
            {
                "vers": "0.5.5",
                "yanked": yanked,
                "features": features if features is not None else {"api-4-2": []},
                "features2": features2 or {},
            }
        ).encode()
    )


@pytest.mark.parametrize(
    ("crate", "godot"),
    [
        ("^0.5.5", "4.2"),
        ("0.5", "4.2"),
        ("0.5.5-rc.1", "4.2"),
        ("0.05.5", "4.2"),
        ("0.5.5", "3.6"),
        ("0.5.5", "4.1"),
        ("0.5.5", "4.2-dev"),
        ("0.5.5", "4.02"),
    ],
)
def test_invalid_versions_rejected_without_network(crate, godot):
    with patch("godot_rust_scaffold.cli.urlopen") as fetch:
        with pytest.raises(ValueError):
            validate_versions(crate, godot, False)
        fetch.assert_not_called()


@pytest.mark.parametrize(("version", "yanked"), [("9.9.9", False), ("0.5.5", True)])
def test_unknown_and_yanked_crates_rejected(version, yanked):
    with (
        patch("godot_rust_scaffold.cli.urlopen", return_value=registry(yanked=yanked)),
        pytest.raises(ValueError),
    ):
        validate_versions(version, "4.2", False)


def test_patch_zero_uses_minor_api():
    with patch("godot_rust_scaffold.cli.urlopen", return_value=registry()):
        assert validate_versions("0.5.5", "4.2.0", False) == "4.2"


def test_nonzero_patch_requires_exact_feature():
    with (
        patch("godot_rust_scaffold.cli.urlopen", return_value=registry()),
        pytest.raises(ValueError, match="does not support"),
    ):
        validate_versions("0.5.5", "4.2.1", False)
    with patch(
        "godot_rust_scaffold.cli.urlopen",
        return_value=registry(features={"api-4-2-1": []}),
    ):
        assert validate_versions("0.5.5", "4.2.1", False) == "4.2.1"


def test_precision_requires_published_feature_in_either_index_map():
    with (
        patch("godot_rust_scaffold.cli.urlopen", return_value=registry()),
        pytest.raises(ValueError, match="double-precision"),
    ):
        validate_versions("0.5.5", "4.2", True)
    with patch(
        "godot_rust_scaffold.cli.urlopen",
        return_value=registry(features2={"double-precision": [], "api-custom": []}),
    ):
        assert validate_versions("0.5.5", "4.2", True) == "4.2"


def test_precision_requires_custom_api_support():
    with (
        patch(
            "godot_rust_scaffold.cli.urlopen",
            return_value=registry(features2={"double-precision": []}),
        ),
        pytest.raises(ValueError, match="api-custom"),
    ):
        validate_versions("0.5.5", "4.2", True)


def test_custom_api_accepts_patch_without_prebuilt_patch_feature():
    with patch(
        "godot_rust_scaffold.cli.urlopen",
        return_value=registry(features2={"double-precision": [], "api-custom": []}),
    ):
        assert validate_versions("0.5.5", "4.2.2", True) == "4.2.2"


@pytest.mark.parametrize(
    ("version", "status", "double"),
    [
        ("4.3.0", "stable", True),
        ("4.2.1", "stable", True),
        ("4.2.0", "dev", True),
        ("4.2.0", "stable", False),
    ],
)
def test_custom_engine_rejects_version_status_and_precision_mismatches(
    version, status, double
):
    info = {"version": version, "status": status, "double": double}
    result = subprocess.CompletedProcess(
        [], 0, "GODOT_RUST_SCAFFOLD:" + json.dumps(info), ""
    )
    with (
        patch("godot_rust_scaffold.cli.subprocess.run", return_value=result),
        pytest.raises(ValueError),
    ):
        validate_double_engine(Path("/custom/godot"), "4.2")


def test_precision_requires_engine_before_network_or_creation(tmp_path):
    with patch("godot_rust_scaffold.cli.urlopen") as fetch:
        result = CliRunner().invoke(
            app,
            [
                "Missing Engine",
                "--parent",
                str(tmp_path),
                "--yes",
                "--double-precision",
            ],
        )
        assert result.exit_code == 1
        assert not (tmp_path / "missing-engine").exists()
        fetch.assert_not_called()


def test_cli_validation_failure_leaves_destination_untouched(tmp_path):
    with patch("godot_rust_scaffold.cli.urlopen", return_value=registry()):
        result = CliRunner().invoke(
            app,
            [
                "Invalid Game",
                "--parent",
                str(tmp_path),
                "--yes",
                "--godot-version",
                "4.99",
            ],
        )
        assert result.exit_code == 1
        assert not (tmp_path / "invalid-game").exists()


def test_registry_failure_leaves_destination_untouched(tmp_path):
    with patch("godot_rust_scaffold.cli.urlopen", side_effect=URLError("offline")):
        result = CliRunner().invoke(
            app,
            [
                "Offline Game",
                "--parent",
                str(tmp_path),
                "--yes",
            ],
        )
        assert result.exit_code == 1
        assert "crates.io" in result.output
        assert not (tmp_path / "offline-game").exists()


@pytest.mark.parametrize(
    "error",
    [
        FileNotFoundError("missing engine"),
        subprocess.CalledProcessError(1, ["godot"]),
        subprocess.TimeoutExpired(["godot"], 30),
    ],
)
def test_custom_engine_execution_failure_is_reported_as_validation_error(error):
    with (
        patch("godot_rust_scaffold.cli.subprocess.run", side_effect=error),
        pytest.raises(ValueError) as failure,
    ):
        validate_double_engine(Path("/custom/godot"), "4.2")

    assert failure.value.__cause__ is error


@pytest.mark.parametrize(
    "stdout",
    [
        "",
        "Godot Engine v4.2.stable\n",
        "GODOT_RUST_SCAFFOLD:not-json\n",
        "GODOT_RUST_SCAFFOLD:{}\n",
    ],
)
def test_custom_engine_requires_valid_version_and_precision_report(stdout):
    result = subprocess.CompletedProcess([], 0, stdout, "")
    with (
        patch("godot_rust_scaffold.cli.subprocess.run", return_value=result),
        pytest.raises(ValueError),
    ):
        validate_double_engine(Path("/custom/godot"), "4.2")


@pytest.mark.parametrize(
    ("requested", "reported"), [("4.2", "4.2.0"), ("4.2.2", "4.2.2")]
)
def test_custom_engine_accepts_exact_stable_double_build(
    tmp_path, monkeypatch, requested, reported
):
    monkeypatch.chdir(tmp_path)
    info = {"version": reported, "status": "stable", "double": True}
    result = subprocess.CompletedProcess(
        [],
        0,
        "Godot startup output\nGODOT_RUST_SCAFFOLD:" + json.dumps(info) + "\n",
        "",
    )
    with patch("godot_rust_scaffold.cli.subprocess.run", return_value=result):
        assert validate_double_engine(Path("engine/godot"), requested) == (
            tmp_path / "engine/godot"
        )


def test_malformed_registry_response_is_rejected():
    with (
        patch("godot_rust_scaffold.cli.urlopen", return_value=io.BytesIO(b"not json")),
        pytest.raises(ValueError),
    ):
        validate_versions("0.5.5", "4.2", False)


def test_registry_allows_blank_lines_and_selects_requested_release():
    older = {"vers": "0.4.5", "yanked": True, "features": {}}
    selected = {
        "vers": "0.5.5",
        "yanked": False,
        "features": {"api-4-2": [], "double-precision": [], "api-custom": []},
    }
    data = ("\n" + json.dumps(older) + "\n\n" + json.dumps(selected) + "\n").encode()
    with patch("godot_rust_scaffold.cli.urlopen", return_value=io.BytesIO(data)):
        assert validate_versions("0.5.5", "4.2", True) == "4.2"
