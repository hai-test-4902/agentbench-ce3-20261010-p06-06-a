#!/bin/sh
# Build the sdist and wheel from one clean checkout inside the pinned
# python:3.12-slim container, then install the wheel into a fresh virtualenv
# and smoke-test it. Invoked by scripts/local_checks.py --build.
#
#   /src        the clean checkout (working directory)
#   $OUT_DIR    receives dist/, SHA256SUMS, <step>.log and <step>.exitcode
#   $SOURCE_DATE_EPOCH  commit time, so the archives are reproducible
#   $EXPECTED_VERSION   version the built distributions must carry
set -u

OUT=${OUT_DIR:-/out}
HOME=${HOME:-/tmp/home}
export HOME
export PIP_DISABLE_PIP_VERSION_CHECK=1
export PIP_ROOT_USER_ACTION=ignore
mkdir -p "$OUT/dist" "$HOME"

failed=0

run_step() {
    step=$1
    shift
    printf '==> %s\n' "$step"
    {
        printf '$ %s\n' "$*"
        "$@"
    } >"$OUT/$step.log" 2>&1
    code=$?
    printf '%s\n' "$code" >"$OUT/$step.exitcode"
    if [ "$code" -ne 0 ]; then
        failed=1
        printf '    FAILED (exit %s), last lines of %s.log:\n' "$code" "$step"
        tail -n 25 "$OUT/$step.log" | sed 's/^/    | /'
    else
        printf '    ok\n'
    fi
    return "$code"
}

build_dists() {
    python -m venv /tmp/build-venv &&
        /tmp/build-venv/bin/python -m pip install --require-hashes -r requirements/build.txt &&
        rm -f "$OUT"/dist/* &&
        /tmp/build-venv/bin/python -m build --no-isolation --outdir "$OUT/dist" . &&
        (cd "$OUT/dist" && sha256sum -- * >"$OUT/SHA256SUMS") &&
        cat "$OUT/SHA256SUMS" &&
        test "$(find "$OUT/dist" -type f | wc -l)" -eq 2
}

wheel_smoke() {
    python -m venv /tmp/smoke-venv &&
        /tmp/smoke-venv/bin/python -m pip install --require-hashes -r requirements/runtime.txt &&
        /tmp/smoke-venv/bin/python -m pip install --no-deps --no-index "$OUT"/dist/*.whl &&
        cd /tmp &&
        version=$(/tmp/smoke-venv/bin/quakewatch --version) &&
        printf '%s\n' "$version" &&
        test "$version" = "quakewatch, version ${EXPECTED_VERSION}" &&
        /tmp/smoke-venv/bin/python -c 'import quakewatch, pathlib; p = pathlib.Path(quakewatch.__file__); assert "site-packages" in p.parts, p; assert (p.parent / "py.typed").is_file()' &&
        /tmp/smoke-venv/bin/quakewatch list --window day --min-mag 4.5 --no-cache --limit 5
}

run_step build build_dists && run_step wheel-smoke wheel_smoke

exit "$failed"
