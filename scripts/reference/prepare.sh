#!/bin/sh
# Export the pinned reference without changing either checkout.
set -eu

if [ "$#" -ne 2 ]; then
    echo "usage: $0 UPSTREAM_CHECKOUT NEW_TEMP_BUILD_CONTEXT" >&2
    exit 2
fi
sha=1aba3764602a0a013ac3fb1ee57ed547dff03f88
scripts=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
upstream=$(CDPATH= cd -- "$1" && pwd)
context=$2
case "$context" in
    /*) ;;
    *) echo 'build context must be an absolute, new temporary directory' >&2; exit 2 ;;
esac
case "$context/" in
    "$upstream/"*|"${scripts%/scripts/reference}/"*)
        echo 'build context must be outside both checkouts' >&2; exit 2 ;;
esac
git -C "$upstream" cat-file -e "$sha^{commit}"
mkdir -m 700 -- "$context"
git -C "$upstream" archive --format=tar "$sha" > "$context/source.tar"
git -C "$upstream" describe --tags --always "$sha" > "$context/version.txt"
printf '%s\n' "$sha" > "$context/upstream-sha.txt"
cp "$scripts/Dockerfile" "$context/"
cp "$scripts/run.py" "$context/"
cp "$scripts/../../tests/reference/rename_gate.c" "$context/"
printf 'Prepared %s at %s\n' "$sha" "$context"
