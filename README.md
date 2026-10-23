# godot-rust-scaffold

CLI for creating a Rust + Godot project using the [godot-rust book’s recommended layout](https://godot-rust.github.io/book/intro/hello-world.html).

## Requirements

- [uv](https://docs.astral.sh/uv/) and Python 3.12+
- Git and Rust 1.94+ (including Cargo)
- Internet access to the crates.io index for version verification and dependencies
- Godot at least as new as the selected API version (4.2 by default)

## Install and run

Install from local clone:

```sh
uv tool install .
```

Or directly from git:

```sh
uv tool install git+https://github.com/kthwaite/godot-rust-scaffold
```

By default the CLI runs interactively, prompting for a name, destination, renderer,
precision, API version, and confirmation; existing project directories won't be
overwritten.

```sh
godot-rust-scaffold
```

Use the ↑/↓ arrow keys to highlight a choice, then Enter to select it. Each picker
starts on its default; Ctrl+C cancels setup before any project files are created.

- **Renderer:** `forward+`, `mobile`, or `compatibility` (default).
- **Precision:** `single` (f32, default) or `double` (f64).
- **Godot API version:** versions supported by the selected `godot` crate, fetched
  from crates.io. Defaults to `4.2` when supported, otherwise the lowest supported
  version of at least 4.2.

Explicit `--renderer`, `--double-precision`, and `--godot-version` options skip
their respective selection prompts. Double precision also prompts for a custom
Godot executable unless `--godot-bin` is supplied; the engine is verified before
creation. Use `--godot-version` to request a custom engine patch version that isn't
in the prebuilt API list.

Noninteractive mode skips all prompts and keeps the defaults for omitted settings
(parent dir must already exist):

```sh
godot-rust-scaffold "My Game" --parent ~/Projects --yes
```

## Rendering backend

Use `--renderer` in either interactive or noninteractive mode, or choose from the
interactive list. Compatibility remains the default; unknown choices are rejected
before setup starts.

```sh
godot-rust-scaffold "My Game" --parent ~/Projects --yes --renderer forward+
```

| Choice          | Desktop renderer                   | Mobile override                    |
| --------------- | ---------------------------------- | ---------------------------------- |
| `forward+`      | Forward+ (`forward_plus`)          | Mobile (`mobile`)                  |
| `mobile`        | Mobile (`mobile`)                  | Mobile (`mobile`)                  |
| `compatibility` | Compatibility (`gl_compatibility`) | Compatibility (`gl_compatibility`) |

The selection sets the feature tag and both rendering-method settings in
`godot/project.godot`. Forward+ keeps Godot's standard Mobile override for mobile
devices; choosing Mobile explicitly also selects it on desktop.

## Versions and precision

Options work in both interactive and noninteractive mode:

```sh
godot-rust-scaffold "My Game" --parent ~/Projects --yes \
  --godot-crate-version 0.5.5 --godot-version 4.6
```

| Option                  | Default | Meaning                                                                                                                                 |
| ----------------------- | ------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| `--godot-crate-version` | `0.5.5` | Exact stable release of the `godot` Rust crate; pinned with `=` in Cargo.toml.                                                          |
| `--godot-version`       | `4.2`   | Godot API level and minimum runtime version; sets the Cargo `api-*` feature, project version, and `.gdextension` compatibility minimum. |
| `--double-precision`    | Off     | Enables the crate's `double-precision` feature, making `godot::builtin::real` an `f64` instead of `f32`.                                |
| `--godot-bin`           | Unset   | Required with double precision: path to a custom Godot editor executable matching the selected version exactly.                         |

Before creating files, the CLI checks the [crates.io index](https://index.crates.io/go/do/godot)
for a published, non-yanked crate release and the requested features. Invalid or
unsupported combinations, or a failed registry lookup, stop setup without creating
the project. Cargo version ranges and prereleases are not accepted.

Godot versions use `4.MINOR` or `4.MINOR.PATCH`, with a minimum of 4.2. Patch zero
is normalized to the minor API (`4.6.0` becomes `4.6`); other patches require an
exact feature in the selected crate for standard precision. There is no silent
fallback to an older API.
The project's feature tag uses the minor version, while `.gdextension` retains any
requested nonzero patch minimum. See [godot-rust's API version guidance](https://godot-rust.github.io/book/toolchain/godot-version.html).

### Double precision

**Double precision requires a Godot editor compiled with `precision=double`.**
Standard Godot downloads are incompatible. The bindings also
[require a custom API for double precision](https://github.com/godot-rust/gdext/blob/v0.5.5/godot/src/lib.rs),
so the CLI selects `api-custom` instead of `api-4-*`:

```sh
godot-rust-scaffold "Large World" --parent ~/Projects --yes \
  --godot-version 4.6 --double-precision --godot-bin /path/to/godot-double
```

Before creating the project, the CLI runs a bundled headless script in the supplied
engine to verify its stable version and double-precision build. The engine must
match exactly: `--godot-version 4.6` means 4.6.0; use `4.6.2` for a 4.6.2 engine.
Custom APIs permit nonzero patches without a prebuilt patch feature, but the crate
must support the engine's minor version and `api-custom`.

The absolute engine path is saved in `rust/.cargo/config.toml` for subsequent
builds; update it when moving the project or sharing it with another developer.
Install [LLVM/libclang as required by api-custom](https://godot-rust.github.io/book/intro/setup.html#llvm).
The CLI does not install Godot or LLVM. Use the same double-precision engine to run
the project. See also the [crate's precision documentation](https://docs.rs/godot/0.5.5/godot/#cargo-features).

Crate overrides may require a newer Rust toolchain or changes to the starter Rust
code; the final Cargo build checks compilation.

## Build Rust inside Godot

New projects include an enabled GDScript editor plugin in
`godot/addons/rust_build/`. No Python process is needed after scaffolding.

1. Save your Rust source files in your code editor and stop any running game.
2. Click **Build Rust** in Godot's top toolbar.
3. Open the **Rust Build** bottom panel for the result and compiler output.
4. Run the game again to use the rebuilt library.

Cargo runs on a worker thread so the editor stays responsive. Output, including
compiler errors, appears when the process finishes, not as a live stream.
Overlapping builds and starting a game during a build are blocked. This is a
manual debug build; pressing Play does not automatically compile Rust.

The plugin builds the sibling `rust/Cargo.toml`, explicitly loads
`rust/.cargo/config.toml` when present, and writes to `rust/target`. This preserves
the selected API/precision features and the custom Godot executable configured
for double precision. Custom Cargo targets still need matching `.gdextension`
paths.

The **Cargo executable** field accepts an executable name or an absolute path,
without shell quotes or arguments. It defaults to Cargo under `CARGO_HOME` or
the usual `~/.cargo/bin` location when available, otherwise `cargo` on `PATH`.
The value is saved when building, in Godot's local project metadata under
`.godot/`, not in the shared project configuration.

**A successful build does not mean the editor has reloaded the extension.**
When supported by the engine/bindings combination, Godot's normal hot reload
runs after switching away from the editor and back. Otherwise use
**Save & Restart Editor** in the panel to save scenes and refresh editor classes.
For example, Godot 4.7.2 disabled hot reload for the default API-4.2 extension
during verification. Restarting the game loads the new library independently.
See [godot-rust's reload guidance](https://godot-rust.github.io/book/intro/hello-world.html#the-gdextension-file).

There is no build cancellation: closing the editor or disabling the plugin
while a build is active waits for Cargo to finish. Wait for completion first.
Existing generated projects are not modified by upgrading the CLI; to add the
plugin, copy `addons/rust_build/` from a newly generated project's `godot/`
directory and enable **Rust Build** under **Project → Project Settings → Plugins**.

## Development

Install the project and development dependencies (including pytest and pytest-cov),
then run the test suite:

```sh
uv sync
uv run pytest
```

Measure statement and branch coverage, including uncovered line numbers:

```sh
uv run pytest --cov=godot_rust_scaffold --cov-branch --cov-report=term-missing
```

Tests exercise CLI prompts, validation, generated project settings, destination
protection, and failure recovery using temporary directories. Registry responses
and external commands are mocked; the suite needs no network, Git, Cargo, or Godot.
It does not replace building and running a generated project with the real tools.
