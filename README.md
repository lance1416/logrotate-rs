# logrotate-rs

A Rust reimplementation of [logrotate](https://github.com/logrotate/logrotate).
The goal is to reproduce its configuration and log rotation behavior.

The executable currently parses CLI arguments, prints help/version information,
and checks metadata for explicitly supplied configuration files or directories.
It does not read configuration contents, enumerate directories, produce rotation
plans, or rotate logs. Valid normal and dry-run requests both return an explicit
not-implemented error with status 1.

## Current CLI

```text
logrotate-rs [OPTIONS] CONFIG [CONFIG ...]

  -h, --help                 Print help (exit 0)
      --version              Print the Cargo package version (exit 0)
  -d, --debug, --dry-run      Select read-only mode; planner is not implemented
  -f, --force                 Record force intent; executor is not implemented
  -s, --state PATH            Save a state path without opening it
  -v, --verbose              Write detailed diagnostics to stderr
      --                     End options; remaining arguments are paths
```

For example, `cargo run --locked -- --help` prints current usage.
The default state path is `/var/lib/logrotate-rs/status`; this version never opens
it. No configuration path is selected implicitly. Options can appear between
configuration paths, whose order is retained. Paths containing spaces are passed
as single shell arguments. Use `--` before a configuration filename beginning
with `-`; a lone `-` is a filename, not stdin.

Boolean flags may repeat, with the same effect as one occurrence. The last state
option wins. `--state=PATH` and `-sPATH` are accepted, as are short clusters such
as `-dfv`. Within a cluster, `s` takes the remaining suffix as its value, or the
next argument if there is no suffix. A separate state value beginning with `-`
(except a lone `-`) requires an attached form such as `--state=-name`.
Empty state values are rejected.

Parsing proceeds left to right. The first help/version option returns immediately
without checking any configuration or state, including paths supplied earlier.
An earlier invalid option or missing state value still fails. After `--`, even
`--help` is a configuration filename. Help/version output goes to stdout;
diagnostics go to stderr. Exit codes currently used are:

| Code | Meaning |
| --- | --- |
| 0 | Help/version successfully printed |
| 1 | Missing CONFIG, entry metadata/type error, or unavailable executor/planner |
| 2 | Unknown/unsupported option or invalid/missing option value |

Metadata checks follow symlinks and accept targets that are regular files or
directories. They report missing/inaccessible entries and reject FIFOs, devices
and sockets without opening them. Content readability, configuration syntax,
full path safety and permissions policy are not yet validated. Configuration
contents and state are never opened. Lock/pending files are untouched; hooks,
shells and compressors are not run. No business file is written, and no successful
dry-run plan is produced.

Native OS paths are retained without lossy conversion. On Unix, positional paths
and separate state values can contain non-UTF-8 bytes; unsupported filesystem names
receive an entry error. Option tokens themselves must be UTF-8, including attached
state values; a non-UTF-8 option token is a parse error. These rules also apply to
help/version precedence. Full logrotate compatibility is not claimed.

## Development

Install [rustup](https://rustup.rs/), then run:

```sh
./scripts/setup-dev.sh
./scripts/check.sh
```

`rust-toolchain.toml` pins Rust 1.99.1 with rustfmt, Clippy, rust-analyzer, and Rust
sources. Rust 2024 is the project edition. The executable has no third-party Rust
dependencies. `Cargo.lock` is committed for reproducible builds.

Tool versions and official sources:

| Component | Version | Source |
| --- | --- | --- |
| Rust toolchain | 1.99.1 (stable) | [Stable channel manifest](https://static.rust-lang.org/dist/channel-rust-stable.toml) |
| `actions/checkout` | v7.0.1, pinned by commit SHA | [Release](https://github.com/actions/checkout/releases/tag/v7.0.1) |

Useful commands:

```sh
cargo fmt --all
cargo lint
cargo test-all
cargo build --locked --release
```

VS Code extension recommendations and format-on-save settings are included.
Other editors can use the toolchain's rust-analyzer and rustfmt.

CI runs formatting, Clippy, tests, documentation, and release builds on the current
GitHub-hosted Ubuntu and macOS images. Dependabot checks Cargo dependencies and
GitHub Actions weekly. Rust toolchain updates are manual: update
`rust-toolchain.toml`, the package's `rust-version`, and this document together.

## Commits

Use an English Conventional Commit subject only, with no body or trailers
(including Co-Author lines):

```text
chore: bootstrap Rust project
```

The setup script enables a local commit message hook that checks the Conventional
Commit structure and rejects bodies and trailers. Contributors remain responsible
for writing the subject in English. Dependabot generates its own PR commits; when
merging those PRs, use a squash commit following the same subject-only convention.

## License

GPL-2.0-or-later; see [LICENSE](LICENSE). This matches upstream logrotate's
GPLv2-or-later licensing.
