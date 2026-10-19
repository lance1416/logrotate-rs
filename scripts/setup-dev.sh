#!/bin/sh
set -eu

cd "$(dirname "$0")/.."

# rustup reads rust-toolchain.toml and installs the pinned toolchain/components.
rustup show active-toolchain
git config --local core.hooksPath .githooks

printf '%s\n' 'Development toolchain ready; commit message hook enabled.'
