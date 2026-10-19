#!/bin/sh
set -eu

cd "$(dirname "$0")/.."

cargo fmt --all -- --check
cargo lint
cargo test-all
RUSTDOCFLAGS="-D warnings" cargo doc --locked --no-deps --all-features
cargo build --locked --release
