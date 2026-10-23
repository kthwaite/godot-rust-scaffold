"""Create a godot project with Rust extension, following the godot-rust book's sibling
godot/ and rust/ project layout.

Requires Git, Rust 1.94+ (cargo), and internet access to crates.io.

The .gdextension paths are for local development. Before exporting, place
compiled libraries inside godot/ and update those paths (Godot cannot export
res://../ paths). Custom Cargo targets also require updating library paths.
"""

import json
import re
import shlex
import shutil
import subprocess
import tempfile
from enum import StrEnum
from importlib.resources import files
from pathlib import Path
from typing import Annotated
from urllib.error import URLError
from urllib.request import urlopen

import typer
from jinja2 import Environment, StrictUndefined
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

from godot_rust_scaffold import resources

app = typer.Typer(pretty_exceptions_enable=False, no_args_is_help=True)
console = Console(markup=False)
errors = Console(stderr=True, markup=False)


class Renderer(StrEnum):
    """Godot rendering backend for the project."""

    FORWARD_PLUS = "forward+"
    MOBILE = "mobile"
    COMPATIBILITY = "compatibility"


def run(*args: str | Path, cwd: Path) -> None:
    """Printing a command to the console and run it with `subprocess`.

    Raises:
        CalledProcessError: on nonzero exit.

    """
    console.print(f"+ {shlex.join(str(arg) for arg in args)}", style="dim")
    subprocess.run([str(arg) for arg in args], cwd=cwd, check=True)


def project_slug(name: str) -> tuple[str, str]:
    """Generate a project slug from a project name.

    Keeps user-facing titles separate from filesystem and Rust identifiers.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    if not slug:
        raise ValueError(
            "Project name must contain at least one ASCII letter or digit."
        )
    # A prefix also avoids Rust keywords and Cargo-reserved package names.
    return slug, "game_" + slug.replace("-", "_")


def validate_versions(
    crate_version: str, godot_version: str, double_precision: bool
) -> str:
    """Verify published Cargo features before creating any project files."""
    number = r"(?:0|[1-9][0-9]*)"
    if not re.fullmatch(rf"{number}\.{number}\.{number}", crate_version):
        raise ValueError(
            "Godot crate version must be an exact stable release, e.g. 0.5.5."
        )
    if not re.fullmatch(rf"4\.{number}(?:\.{number})?", godot_version):
        raise ValueError("Godot version must be 4.MINOR or 4.MINOR.PATCH, e.g. 4.6.")
    parts = godot_version.split(".")
    if int(parts[1]) < 2:
        raise ValueError("This scaffold requires Godot 4.2 or newer.")
    # Patch zero uses the minor-level feature (api-4-6, not api-4-6-0).
    if len(parts) == 3 and parts[2] == "0":
        godot_version = ".".join(parts[:2])
    try:
        with urlopen("https://index.crates.io/go/do/godot", timeout=20) as response:
            releases = [json.loads(line) for line in response if line.strip()]
    except (OSError, URLError, ValueError) as error:
        raise ValueError(
            f"Could not verify versions with crates.io: {error}"
        ) from error
    release = next(
        (release for release in releases if release["vers"] == crate_version), None
    )
    if release is None:
        raise ValueError(f"godot crate {crate_version} is not published on crates.io.")
    if release["yanked"]:
        raise ValueError(
            f"godot crate {crate_version} is yanked; choose another release."
        )
    features = release["features"] | release.get("features2", {})
    api_version = (
        ".".join(godot_version.split(".")[:2]) if double_precision else godot_version
    )
    api_feature = "api-" + api_version.replace(".", "-")
    if api_feature not in features:
        supported = ", ".join(
            feature.removeprefix("api-").replace("-", ".")
            for feature in features
            if feature.startswith("api-4-")
        )
        raise ValueError(
            f"godot crate {crate_version} does not support Godot API {godot_version}. "
            f"Supported API versions: {supported or 'none'}."
        )
    if double_precision:
        for feature in ("double-precision", "api-custom"):
            if feature not in features:
                raise ValueError(
                    f"godot crate {crate_version} does not support {feature}."
                )
    return godot_version


def validate_double_engine(binary: Path, godot_version: str) -> Path:
    """Check the custom API's actual version and ABI before generating a project.

    In particular we want to ensure that 1) the engine's version matches the requested
    API version, and 2) if double-precision is requested, that the engine was compiled
    with precision=double.
    """
    binary = binary.expanduser().resolve()
    with tempfile.TemporaryDirectory(prefix="godot-rust-engine-") as directory:
        script = Path(directory) / "verify_engine.gd"
        script.write_text(
            files(resources).joinpath("verify_engine.gd").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        try:
            result = subprocess.run(
                [
                    str(binary),
                    "--headless",
                    "--path",
                    directory,
                    "--script",
                    str(script),
                ],
                cwd=directory,
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise ValueError(
                f"Could not inspect Godot engine {binary}: {error}"
            ) from error
    prefix = "GODOT_RUST_SCAFFOLD:"
    for line in result.stdout.splitlines():
        if line.startswith(prefix):
            info = json.loads(line.removeprefix(prefix))
            break
    else:
        raise ValueError(
            f"Godot engine {binary} did not report its version and precision."
        )
    expected = godot_version if godot_version.count(".") == 2 else godot_version + ".0"
    if info.get("version") != expected or info.get("status") != "stable":
        raise ValueError(
            f"Custom Godot engine must match --godot-version {expected} (stable); "
            f"found {info.get('version')} ({info.get('status')})."
        )
    if info.get("double") is not True:
        raise ValueError("Custom Godot engine must be compiled with precision=double.")
    return binary


def create_project(
    name: str,
    parent: Path,
    *,
    crate_version: str,
    godot_version: str,
    double_precision: bool,
    godot_bin: Path | None,
    renderer: Renderer,
) -> Path:
    """Create a new Godot + Rust project in a sibling godot/ and rust/ layout.

    Args:
        name: Project title; used for the Godot project and Rust crate.
        parent: Existing directory to create the project in.
        crate_version: Exact stable godot crate release on crates.io.
        godot_version: Godot API/minimum runtime version (4.MINOR or supported
            4.MINOR.PATCH).
        double_precision: Use f64 real; requires --godot-bin and LLVM/libclang for
            api-custom.
        godot_bin: Optional double-precision Godot executable matching --godot-version exactly.
        renderer: Godot rendering backend for the project.

    Raises:
        ValueError: on invalid arguments or missing commands.
    """
    slug, crate = project_slug(name)
    renderer_feature, rendering_method = {
        Renderer.FORWARD_PLUS: ("Forward Plus", "forward_plus"),
        Renderer.MOBILE: ("Mobile", "mobile"),
        Renderer.COMPATIBILITY: ("GL Compatibility", "gl_compatibility"),
    }[renderer]
    for tool in ("cargo", "git"):
        if shutil.which(tool) is None:
            raise ValueError(
                f"Required command not found: {tool}. Install it and retry."
            )
    if not parent.is_dir():
        raise ValueError(f"Parent directory does not exist: {parent}")

    root = parent / slug
    # Atomic mkdir: never reuse or overwrite an existing project, even a symlink.
    root.mkdir()
    try:
        console.rule("Step 4 · Create project files")
        run("git", "init", cwd=root)
        run(
            "cargo",
            "new",
            "rust",
            "--lib",
            "--name",
            crate,
            "--edition",
            "2021",
            "--vcs",
            "none",
            cwd=root,
        )
        godot = root / "godot"
        environment = Environment(
            undefined=StrictUndefined,
            autoescape=False,
            keep_trailing_newline=True,
            trim_blocks=True,
        )
        resource_root = files(resources)
        for resource_name, destination in (
            ("Cargo.toml.j2", "rust/Cargo.toml"),
            ("lib.rs", "rust/src/lib.rs"),
            ("config.toml.j2", "rust/.cargo/config.toml"),
            ("gitignore", ".gitignore"),
            ("project.godot.j2", "godot/project.godot"),
            ("main.tscn", "godot/main.tscn"),
            ("game.gdextension.j2", "godot/game.gdextension"),
            ("extension_list.cfg", "godot/.godot/extension_list.cfg"),
        ):
            content = resource_root.joinpath(resource_name).read_text(encoding="utf-8")
            if resource_name.endswith(".j2"):
                content = environment.from_string(content).render(
                    crate=crate,
                    project_name_literal=json.dumps(name, ensure_ascii=False),
                    crate_version=crate_version,
                    godot_version=godot_version,
                    godot_minor_version=".".join(godot_version.split(".")[:2]),
                    renderer_feature=renderer_feature,
                    rendering_method=rendering_method,
                    mobile_rendering_method=(
                        "mobile"
                        if renderer == Renderer.FORWARD_PLUS
                        else rendering_method
                    ),
                    api_feature=(
                        "api-custom"
                        if double_precision
                        else "api-" + godot_version.replace(".", "-")
                    ),
                    double_precision=double_precision,
                    godot_bin_literal=json.dumps(str(godot_bin), ensure_ascii=False),
                    godot_bin_env=(
                        "GDRUST_GODOT_BIN"
                        if tuple(map(int, crate_version.split("."))) >= (0, 5, 0)
                        else "GODOT4_BIN"
                    ),
                )
            path = root / destination
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        console.rule("Step 5 · Build Rust extension")
        run(
            "cargo",
            "build",
            "--target-dir",
            root / "rust" / "target",
            cwd=root / "rust",
        )
    except (OSError, subprocess.CalledProcessError, KeyboardInterrupt):
        errors.print(
            f"Setup did not finish. Files are preserved at {root}.", style="red"
        )
        errors.print(
            "Fix the reported error. If all files were generated, rebuild with "
            "cargo build from rust/.\n"
            "To regenerate, choose a new name or move the incomplete directory."
        )
        raise
    console.print(
        f"\nCreated {root}\n"
        f"Open {godot / 'project.godot'} in Godot {godot_version}+ and press F6/F5.\n"
        "The Output panel should say: Hello from Rust! Your GDExtension is running.\n\n"
        f"Rebuild: cd {shlex.quote(str(root / 'rust'))} && cargo build\n"
        "Release: cargo build --release (from rust/)\n"
        "Commit Cargo.lock; generated caches and build output are ignored.\n"
        "These paths use the native Cargo target. Custom targets/target directories\n"
        "require matching .gdextension paths. For export, move libraries inside godot/."
    )
    return root


@app.command()
def main(
    name: Annotated[
        str | None, typer.Argument(help="Project title; prompted when omitted.")
    ] = None,
    parent: Annotated[
        Path | None,
        typer.Option(
            "--parent", help="Existing destination directory; prompted when omitted."
        ),
    ] = None,
    godot_crate_version: Annotated[
        str,
        typer.Option(
            "--godot-crate-version",
            help="Exact stable godot crate release on crates.io.",
        ),
    ] = "0.5.5",
    godot_version: Annotated[
        str,
        typer.Option(
            "--godot-version",
            help="Godot API/minimum runtime version (4.MINOR or supported 4.MINOR.PATCH).",
        ),
    ] = "4.2",
    double_precision: Annotated[
        bool,
        typer.Option(
            "--double-precision",
            help="Use f64 real; requires --godot-bin and LLVM/libclang for api-custom.",
        ),
    ] = False,
    godot_bin: Annotated[
        Path | None,
        typer.Option(
            "--godot-bin",
            help="Double-precision Godot executable matching --godot-version exactly.",
        ),
    ] = None,
    renderer: Annotated[
        Renderer,
        typer.Option("--renderer", help="Godot rendering backend."),
    ] = Renderer.COMPATIBILITY,
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Skip prompts; requires NAME and --parent."),
    ] = False,
) -> None:
    """Create a Rust + Godot project, wire its extension, and build it."""
    console.print(
        Panel(
            "Godot + Rust project setup\n"
            "Sibling godot/ and rust/ directories · Godot 4.2+ · Rust 1.94+\n"
            "Git and Cargo must be installed; the first build needs crates.io access.",
            title="godot-rust-scaffold",
            border_style="cyan",
        )
    )
    try:
        if yes and (name is None or parent is None):
            raise ValueError(
                "--yes requires NAME and --parent so setup never waits for input."
            )
        console.rule("Step 1 · Name your project")
        name = (
            name if name is not None else Prompt.ask("Project name", console=console)
        ).strip()
        if not name or any(ord(char) < 32 for char in name):
            raise ValueError("Use a nonempty project name without control characters.")
        slug, crate = project_slug(name)

        console.rule("Step 2 · Choose a destination")
        if parent is None:
            parent = Path(
                Prompt.ask("Parent directory", default=str(Path.cwd()), console=console)
            )
        parent = parent.expanduser().resolve()
        if not parent.is_dir():
            raise ValueError(f"Parent directory does not exist: {parent}")
        root = parent / slug
        if root.exists() or root.is_symlink():
            raise ValueError(f"Destination already exists; nothing was changed: {root}")

        if double_precision and godot_bin is None:
            raise ValueError("--double-precision requires --godot-bin PATH.")
        if godot_bin is not None and not double_precision:
            raise ValueError("--godot-bin is only used with --double-precision.")
        godot_version = validate_versions(
            godot_crate_version, godot_version, double_precision
        )
        if godot_bin is not None:
            godot_bin = validate_double_engine(godot_bin, godot_version)
        console.rule("Step 3 · Review")
        summary = Table(show_header=False, box=None)
        for label, value in (
            ("Project", name),
            ("Directory", str(root)),
            ("Rust crate", crate),
            ("Bindings", f"godot {godot_crate_version} · API {godot_version}"),
            ("Precision", "double (f64)" if double_precision else "single (f32)"),
            ("Renderer", renderer.value),
            (
                "Actions",
                "Initialize Git, create files, download dependencies, build extension",
            ),
        ):
            summary.add_row(label, value)
        console.print(summary)
        if double_precision:
            console.print(
                f"Custom double-precision engine: {godot_bin}\n"
                "Building api-custom requires LLVM/libclang. Rebuilds use this engine; "
                "standard Godot downloads are incompatible.",
                style="yellow",
            )
        if not yes and not Confirm.ask(
            "Create this project?", default=True, console=console
        ):
            console.print("Cancelled. No files were created.")
            raise typer.Exit()
        create_project(
            name,
            parent,
            crate_version=godot_crate_version,
            godot_version=godot_version,
            double_precision=double_precision,
            godot_bin=godot_bin,
            renderer=renderer,
        )
    except (ValueError, OSError, subprocess.CalledProcessError, EOFError) as error:
        errors.print(f"Error: {error}", style="red")
        raise typer.Exit(code=1) from None
    except KeyboardInterrupt:
        errors.print("\nCancelled.", style="yellow")
        raise typer.Exit(code=130) from None
    console.print(
        "Setup complete. Open godot/project.godot and press F5.", style="green"
    )
