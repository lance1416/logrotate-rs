#!/bin/sh
# Export the pinned reference without changing either checkout.
set -eu

if [ "$#" -ne 2 ]; then
    echo "usage: $0 UPSTREAM_CHECKOUT NEW_TEMP_BUILD_CONTEXT" >&2
    exit 2
fi
sha=1aba3764602a0a013ac3fb1ee57ed547dff03f88
# Resolve both directory aliases and a symlink to the script itself before
# locating its companion files and the protected main checkout.
script=$0
while :; do
    scripts=$(CDPATH= cd -P -- "$(dirname -- "$script")" && pwd -P)
    script=$scripts/$(basename -- "$script")
    [ -L "$script" ] || break
    link=$(readlink -- "$script")
    case "$link" in
        /*) script=$link ;;
        *) script=$scripts/$link ;;
    esac
done
main=$(CDPATH= cd -P -- "$scripts/../.." && pwd -P)
upstream=$(git -C "$1" rev-parse --show-toplevel)
upstream=$(CDPATH= cd -P -- "$upstream" && pwd -P)
context=$2
case "$context" in
    /*) ;;
    *) echo 'build context must be an absolute, new temporary directory' >&2; exit 2 ;;
esac
# mkdir requires an existing parent. Resolve that parent physically, preserving
# symlink/.. traversal semantics, before any directory creation or archive export.
parent=$(CDPATH= cd -P -- "$(dirname -- "$context")" && pwd -P)
context=${parent%/}/$(basename -- "$context")
case "$context/" in
    "$upstream/"*|"$main/"*)
        echo 'build context must be outside both checkouts' >&2; exit 2 ;;
esac
if [ -e "$context" ] || [ -L "$context" ]; then
    echo 'build context must not already exist' >&2
    exit 2
fi
git -C "$upstream" cat-file -e "$sha^{commit}"
mkdir -m 700 -- "$context"
git -C "$upstream" archive --format=tar "$sha" > "$context/source.tar"
git -C "$upstream" describe --tags --always "$sha" > "$context/version.txt"
printf '%s\n' "$sha" > "$context/upstream-sha.txt"
cp "$scripts/Dockerfile" "$context/"
cp "$scripts/run.py" "$context/"
cp "$scripts/../../tests/reference/rename_gate.c" "$context/"
printf 'Prepared %s at %s\n' "$sha" "$context"
