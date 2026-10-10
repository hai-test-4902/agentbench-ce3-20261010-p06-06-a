#!/bin/sh
# Lint, type-check and test one clean checkout inside a python:<version>-slim
# container. Invoked by scripts/local_checks.py - not meant to be run on a host.
#
#   /src        the clean checkout (working directory)
#   $OUT_DIR    receives <step>.log, <step>.exitcode and the coverage files
#   $PIP_CACHE_DIR  workspace-local pip cache
#   $RUN_INTEGRATION=1  also run the live-API tests
#
# Every step runs even if an earlier one failed (except when the install
# itself fails), so one run reports everything that is wrong.
set -u

OUT=${OUT_DIR:-/out}
VENV=${VENV_DIR:-/tmp/venv}
HOME=${HOME:-/tmp/home}
export HOME
export PIP_DISABLE_PIP_VERSION_CHECK=1
export PIP_ROOT_USER_ACTION=ignore
export PYTHONDONTWRITEBYTECODE=1
export COVERAGE_FILE="$OUT/.coverage"
export RUFF_NO_CACHE=true
mkdir -p "$OUT" "$HOME"

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

install() {
    python -m venv "$VENV" &&
        "$VENV/bin/python" -m pip install --require-hashes -r requirements/dev.txt &&
        "$VENV/bin/python" -m pip install --no-deps --no-build-isolation -e .
}

unit_tests() {
    python -m pytest -m "not integration" -p no:cacheprovider \
        --cov=quakewatch --cov-branch --cov-fail-under=85 \
        --cov-report=term-missing \
        --cov-report="xml:$OUT/coverage.xml" \
        --cov-report="json:$OUT/coverage.json" \
        --junitxml="$OUT/unit-junit.xml"
}

integration_tests() {
    python -m pytest -m integration -p no:cacheprovider -v \
        --junitxml="$OUT/integration-junit.xml"
}

{
    python -c 'import sys; print("python_version=%d.%d" % sys.version_info[:2])'
    python -c 'import sys; print("python_full_version=" + sys.version.split()[0])'
    python -c 'import platform; print("platform=" + platform.platform())'
    printf 'uid=%s\n' "$(id -u)"
} >"$OUT/environment.txt"

if run_step install install; then
    PATH="$VENV/bin:$PATH"
    export PATH
    python -m pip freeze --all >"$OUT/pip-freeze.txt" 2>&1
    run_step ruff-check ruff check .
    run_step ruff-format ruff format --check .
    run_step mypy mypy --strict --cache-dir /tmp/mypy-cache
    run_step unit unit_tests
    if [ "${RUN_INTEGRATION:-0}" = "1" ]; then
        run_step integration integration_tests
    fi
fi

exit "$failed"
