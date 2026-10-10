#!/bin/sh
# Regenerate the hash-pinned lock files in requirements/ from pyproject.toml.
# Needs uv (https://docs.astral.sh/uv/); put it on PATH or in ./.tools/bin.
set -eu

cd "$(dirname "$0")/.."
PATH="$PWD/.tools/bin:$PATH"
UV_CACHE_DIR="${UV_CACHE_DIR:-$PWD/.cache/uv}"
export PATH UV_CACHE_DIR

compile() {
    uv pip compile --quiet --universal --python-version 3.10 --generate-hashes --no-header "$@"
}

compile pyproject.toml -o requirements/runtime.txt
compile requirements/build.in -o requirements/build.txt
compile pyproject.toml --extra dev requirements/build.in \
    -c requirements/runtime.txt -c requirements/build.txt -o requirements/dev.txt
