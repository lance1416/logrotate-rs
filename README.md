# logrotate-rs

A Rust reimplementation of [logrotate](https://github.com/logrotate/logrotate).
The goal is to reproduce its configuration and log rotation behavior.

This repository currently contains the development scaffold only. The executable
prints a not-implemented diagnostic and exits with status 1. It does not rotate
logs yet.

## Development

Install [rustup](https://rustup.rs/), then run:

```sh
./scripts/setup-dev.sh
./scripts/check.sh
```

`rust-toolchain.toml` pins Rust 1.99.1 with rustfmt, Clippy, rust-analyzer, and Rust
sources. Rust 2024 is the project edition. The scaffold has no third-party Rust
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
