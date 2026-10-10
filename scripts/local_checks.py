#!/usr/bin/env python3
"""Reproducible local check matrix, run in pinned Docker Python containers.

For one commit (default: HEAD) this:

1. exports a clean copy of exactly that commit with ``git archive`` - never the
   working tree, so uncommitted edits, ``.git`` and credential files stay out;
2. for every interpreter in ``local-matrix-images.json`` (pulled by digest)
   runs ruff, ``mypy --strict`` and the unit suite with coverage >= 85 %, and
   on Python 3.12 only, the live integration tests;
3. with ``--build``: builds the sdist and wheel in the 3.12 container, smoke
   tests the wheel, builds the ``agentbench-quakewatch:local`` image from the
   same clean copy and smoke tests ``docker run ... list --window day``.

Logs, exit codes and coverage files for every interpreter are written to
``artifacts/local-checks/<full git sha>/<run id>/`` together with a
``summary.json`` naming the tested commit. Exit status is 0 only if every
stage passed.

Host requirements: git, docker, and any Python 3 to run this file (standard
library only). Python 3.10/3.11/3.12 do not need to be installed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

IMAGE_TAG = "agentbench-quakewatch:local"
CONTAINER_PREFIX = "agentbench-quakewatch-check"
INTEGRATION_PYTHON = "3.12"
BUILD_PYTHON = "3.12"
COVERAGE_FLOOR = 85.0
CHECK_STEPS = ("install", "ruff-check", "ruff-format", "mypy", "unit")
BUILD_STEPS = ("build", "wheel-smoke")

# Names that must never appear in a tree that is mounted into a container.
FORBIDDEN_NAMES = frozenset(
    {".git", ".eval-credentials", ".runtime-secrets", ".env", ".netrc", ".npmrc", ".pypirc"}
)
# The only host environment variables the docker CLI is started with.
DOCKER_ENV_ALLOWLIST = ("PATH", "DOCKER_HOST", "DOCKER_CONTEXT", "XDG_RUNTIME_DIR", "TMPDIR")


class CheckError(RuntimeError):
    """The runner itself could not do its job (as opposed to a check failing)."""


def log(message: str = "") -> None:
    print(message, flush=True)


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args], check=False, capture_output=True, text=True
    )
    if result.returncode != 0:
        raise CheckError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


class Docker:
    """Runs the docker CLI with a scrubbed environment and a workspace-local config dir."""

    def __init__(self, root: Path) -> None:
        config_dir = root / ".cache" / "docker-config"
        config_dir.mkdir(parents=True, exist_ok=True)
        self.env = {name: os.environ[name] for name in DOCKER_ENV_ALLOWLIST if name in os.environ}
        # A private, empty config dir: no registry credentials, no proxy
        # settings injected into containers, nothing written to $HOME.
        self.env["DOCKER_CONFIG"] = str(config_dir)
        self.env["HOME"] = str(config_dir)

    def run(self, *args: str, log_path: Path | None = None, echo: bool = False) -> int:
        """Run ``docker <args>``; output goes to ``log_path`` and/or the console."""
        command = ["docker", *args]
        process = subprocess.Popen(
            command,
            env=self.env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
        )
        assert process.stdout is not None  # noqa: S101 - narrowing for the type checker
        handle = log_path.open("w", encoding="utf-8") if log_path else None
        try:
            for line in process.stdout:
                if handle:
                    handle.write(line)
                if echo:
                    log(f"    {line.rstrip()}")
        finally:
            if handle:
                handle.close()
        return process.wait()

    def output(self, *args: str) -> str:
        result = subprocess.run(
            ["docker", *args],
            env=self.env,
            check=False,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
        )
        if result.returncode != 0:
            raise CheckError(f"docker {' '.join(args)} failed: {result.stderr.strip()}")
        return result.stdout.strip()

    def is_rootless(self) -> bool:
        return "rootless" in self.output("info", "--format", "{{json .SecurityOptions}}")

    def ensure_image(self, reference: str) -> str:
        """Make sure the digest-pinned image is present; return its image ID."""
        if "@sha256:" not in reference:
            raise CheckError(f"image {reference!r} is not pinned by digest")
        probe = subprocess.run(
            ["docker", "image", "inspect", "--format", "{{.Id}}", reference],
            env=self.env,
            check=False,
            capture_output=True,
            text=True,
        )
        if probe.returncode != 0:
            log(f"    pulling {reference}")
            self.output("pull", "--quiet", reference)
        image_id = self.output("image", "inspect", "--format", "{{.Id}}", reference)
        digests = self.output("image", "inspect", "--format", "{{json .RepoDigests}}", reference)
        wanted = reference.split("@", 1)[1]
        if wanted not in digests:
            raise CheckError(f"local image for {reference} does not carry digest {wanted}")
        return image_id


def load_images(path: Path) -> list[dict[str, str]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        images = [
            {"python": str(item["python"]), "image": str(item["image"]), "tag": str(item["tag"])}
            for item in data["images"]
        ]
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise CheckError(f"cannot read image list {path}: {error}") from error
    if not images:
        raise CheckError(f"{path} lists no images")
    return images


def export_commit(root: Path, sha: str, destination: Path) -> None:
    """Materialize the tree of ``sha`` at ``destination`` and prove it is clean."""
    destination.mkdir(parents=True)
    archive = destination.parent / f"{destination.name}.tar"
    git(root, "archive", "--format=tar", "-o", str(archive), sha)
    with tarfile.open(archive) as tar:
        for member in tar.getmembers():
            parts = Path(member.name).parts
            if member.name.startswith("/") or ".." in parts:
                raise CheckError(f"unsafe path in git archive: {member.name}")
        if hasattr(tarfile, "data_filter"):
            tar.extractall(destination, filter="data")
        else:  # Pythons that predate extraction filters; members were validated above
            tar.extractall(destination)  # noqa: S202
    archive.unlink()
    assert_no_secrets(destination)


def assert_no_secrets(tree: Path) -> None:
    for path in tree.rglob("*"):
        if path.name in FORBIDDEN_NAMES:
            raise CheckError(f"refusing to mount {tree}: it contains {path.relative_to(tree)}")


def read_exit_code(path: Path) -> int | None:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def read_environment(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition("=")
            values[key] = value
    return values


def coverage_percent(path: Path) -> float | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return float(data["totals"]["percent_covered"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


class Runner:
    def __init__(self, args: argparse.Namespace) -> None:
        self.root = Path(git(Path.cwd(), "rev-parse", "--show-toplevel"))
        self.ref: str = args.ref
        self.sha = git(self.root, "rev-parse", "--verify", f"{args.ref}^{{commit}}")
        self.tree = git(self.root, "rev-parse", f"{self.sha}^{{tree}}")
        self.commit_epoch = git(self.root, "show", "-s", "--format=%ct", self.sha)
        self.with_build: bool = args.build
        self.keep_work: bool = args.keep_work
        self.images_path = (
            Path(args.images).resolve() if args.images else self.root / "local-matrix-images.json"
        )
        self.images = load_images(self.images_path)
        self.run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.out = self.root / "artifacts" / "local-checks" / self.sha / self.run_id
        self.work = self.root / ".cache" / "local-checks" / f"{self.run_id}-{self.sha[:12]}"
        self.pip_cache = self.root / ".cache" / "pip"
        self.docker = Docker(self.root)
        self.rootless = False

    # -- containers -----------------------------------------------------------

    def container_args(
        self, name: str, image: str, src: Path, out: Path, env: dict[str, str]
    ) -> list[str]:
        """``docker run`` arguments: three bind mounts, an explicit env, nothing else."""
        args = [
            "run",
            "--rm",
            "--pull=never",
            "--name",
            f"{CONTAINER_PREFIX}-{self.run_id.lower()}-{name}",
            "--security-opt=no-new-privileges",
            "--mount",
            f"type=bind,source={src},target=/src",
            "--mount",
            f"type=bind,source={out},target=/out",
            "--mount",
            f"type=bind,source={self.pip_cache},target=/pip-cache",
            "--workdir",
            "/src",
        ]
        if not self.rootless:
            # Rootful daemon: run as the invoking user so outputs are not root-owned.
            # (Under rootless Docker, container root already *is* the invoking user.)
            args += ["--user", f"{os.getuid()}:{os.getgid()}"]
        container_env = {
            "HOME": "/tmp/home",  # noqa: S108 - path inside the container
            "OUT_DIR": "/out",
            "PIP_CACHE_DIR": "/pip-cache",
            **env,
        }
        for key, value in sorted(container_env.items()):
            args += ["--env", f"{key}={value}"]
        args.append(image)
        return args

    def check_version(self, entry: dict[str, str]) -> dict[str, Any]:
        version = entry["python"]
        label = f"py{version}"
        log(f"\n=== Python {version}  ({entry['image']}) ===")
        out = self.out / label
        out.mkdir(parents=True)
        src = self.work / label / "src"
        started = time.monotonic()
        result: dict[str, Any] = {
            "python": version,
            "image": entry["image"],
            "tag": entry["tag"],
            "output_dir": str(out.relative_to(self.root)),
        }
        integration = version == INTEGRATION_PYTHON
        try:
            result["image_id"] = self.docker.ensure_image(entry["image"])
            export_commit(self.root, self.sha, src)
            args = self.container_args(
                label.replace(".", ""),
                entry["image"],
                src,
                out,
                {"RUN_INTEGRATION": "1" if integration else "0"},
            )
            container_exit = self.docker.run(
                *args, "sh", "scripts/ci-in-container.sh", log_path=out / "container.log", echo=True
            )
        except CheckError as error:
            log(f"    ERROR: {error}")
            result.update({"passed": False, "error": str(error)})
            return result

        steps = list(CHECK_STEPS) + (["integration"] if integration else [])
        exit_codes = {step: read_exit_code(out / f"{step}.exitcode") for step in steps}
        environment = read_environment(out / "environment.txt")
        coverage = coverage_percent(out / "coverage.json")
        problems = [
            f"{step}: {'did not run' if code is None else f'exit {code}'}"
            for step, code in exit_codes.items()
            if code != 0
        ]
        if container_exit != 0:
            problems.append(f"container exit {container_exit}")
        if environment.get("python_version") != version:
            problems.append(
                f"container runs Python {environment.get('python_version')}, expected {version}"
            )
        if coverage is None:
            problems.append("no coverage figure produced")
        elif coverage < COVERAGE_FLOOR:
            problems.append(f"coverage {coverage:.2f}% is below {COVERAGE_FLOOR:.0f}%")
        for name in ("coverage.xml", "coverage.json", ".coverage"):
            if not (out / name).is_file():
                problems.append(f"missing {name}")
        result.update(
            {
                "python_full_version": environment.get("python_full_version"),
                "container_exit_code": container_exit,
                "exit_codes": exit_codes,
                "integration": "ran" if integration else f"not run (only on {INTEGRATION_PYTHON})",
                "coverage_percent": None if coverage is None else round(coverage, 2),
                "duration_seconds": round(time.monotonic() - started, 1),
                "problems": problems,
                "passed": not problems,
            }
        )
        (out / "result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        log(
            f"    -> {'PASS' if result['passed'] else 'FAIL'}" + "".join(f"; {p}" for p in problems)
        )
        return result

    # -- build + image smoke test ------------------------------------------------

    def build(self) -> dict[str, Any]:
        log(f"\n=== Build + smoke test (Python {BUILD_PYTHON}) ===")
        out = self.out / "build"
        out.mkdir(parents=True)
        src = self.work / "build" / "src"
        result: dict[str, Any] = {"output_dir": str(out.relative_to(self.root))}
        problems: list[str] = []
        try:
            entry = next(item for item in self.images if item["python"] == BUILD_PYTHON)
        except StopIteration:
            result.update({"passed": False, "problems": [f"no Python {BUILD_PYTHON} image listed"]})
            return result
        try:
            self.docker.ensure_image(entry["image"])
            export_commit(self.root, self.sha, src)
            version = self.project_version(src)
            args = self.container_args(
                "build",
                entry["image"],
                src,
                out,
                {"SOURCE_DATE_EPOCH": self.commit_epoch, "EXPECTED_VERSION": version},
            )
            container_exit = self.docker.run(
                *args,
                "sh",
                "scripts/ci-build-in-container.sh",
                log_path=out / "container.log",
                echo=True,
            )
            exit_codes: dict[str, int | None] = {
                step: read_exit_code(out / f"{step}.exitcode") for step in BUILD_STEPS
            }
            if container_exit != 0:
                problems.append(f"build container exit {container_exit}")
            artifacts = self.describe_artifacts(out / "dist", version, problems)
            exit_codes.update(self.image_smoke(entry["image"], src, out, result))
        except CheckError as error:
            log(f"    ERROR: {error}")
            result.update({"passed": False, "problems": [*problems, str(error)]})
            return result
        problems += [
            f"{step}: {'did not run' if code is None else f'exit {code}'}"
            for step, code in exit_codes.items()
            if code != 0
        ]
        problems += result.pop("smoke_problems", [])
        result.update(
            {
                "version": version,
                "source_date_epoch": int(self.commit_epoch),
                "exit_codes": exit_codes,
                "artifacts": artifacts,
                "problems": problems,
                "passed": not problems,
            }
        )
        (out / "result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        log(
            f"    -> {'PASS' if result['passed'] else 'FAIL'}" + "".join(f"; {p}" for p in problems)
        )
        return result

    @staticmethod
    def project_version(src: Path) -> str:
        in_project = False
        for raw in (src / "pyproject.toml").read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line.startswith("["):
                in_project = line == "[project]"
            elif in_project and line.startswith("version") and "=" in line:
                return line.split("=", 1)[1].strip().strip("\"'")
        raise CheckError("no [project] version in pyproject.toml")

    def describe_artifacts(
        self, dist: Path, version: str, problems: list[str]
    ) -> list[dict[str, Any]]:
        expected = {f"quakewatch-{version}.tar.gz", f"quakewatch-{version}-py3-none-any.whl"}
        files = sorted(path for path in dist.iterdir() if path.is_file()) if dist.is_dir() else []
        if {path.name for path in files} != expected:
            problems.append(
                f"expected {sorted(expected)} in dist/, found {[path.name for path in files]}"
            )
        return [
            {
                "file": path.name,
                "path": str(path.relative_to(self.root)),
                "size": path.stat().st_size,
                "sha256": sha256_file(path),
            }
            for path in files
        ]

    def image_smoke(
        self, base_image: str, src: Path, out: Path, result: dict[str, Any]
    ) -> dict[str, int | None]:
        """Build the runtime image from the clean copy and exercise it."""
        problems: list[str] = []
        codes: dict[str, int | None] = {}
        log("==> docker-build")
        codes["docker-build"] = self.docker.run(
            "build",
            "--pull=false",
            "--build-arg",
            f"PYTHON_IMAGE={base_image}",
            "--label",
            f"org.opencontainers.image.revision={self.sha}",
            "--tag",
            IMAGE_TAG,
            str(src),
            log_path=out / "docker-build.log",
        )
        (out / "docker-build.exitcode").write_text(f"{codes['docker-build']}\n", encoding="utf-8")
        log("    ok" if codes["docker-build"] == 0 else "    FAILED, see docker-build.log")
        if codes["docker-build"] != 0:
            result["smoke_problems"] = problems
            return codes

        inspect = json.loads(self.docker.output("image", "inspect", IMAGE_TAG))[0]
        user = inspect["Config"].get("User", "")
        result["image"] = {
            "tag": IMAGE_TAG,
            "id": inspect["Id"],
            "size_bytes": inspect["Size"],
            "user": user,
            "revision_label": inspect["Config"]
            .get("Labels", {})
            .get("org.opencontainers.image.revision"),
        }
        if user in ("", "0", "root", "0:0", "root:root"):
            problems.append(f"image is configured to run as root (User={user!r})")

        smokes = {
            "docker-smoke": ["list", "--window", "day"],
            "docker-version": ["--version"],
        }
        for step, command in smokes.items():
            log(f"==> {step}: docker run --rm {IMAGE_TAG} {' '.join(command)}")
            name = f"{CONTAINER_PREFIX}-{self.run_id.lower()}-{step}"
            codes[step] = self.docker.run(
                "run", "--rm", "--name", name, IMAGE_TAG, *command, log_path=out / f"{step}.log"
            )
            (out / f"{step}.exitcode").write_text(f"{codes[step]}\n", encoding="utf-8")
            log("    ok" if codes[step] == 0 else f"    FAILED, see {step}.log")

        listing = (out / "docker-smoke.log").read_text(encoding="utf-8").splitlines()
        if not listing or not listing[0].startswith("time_utc\tmagnitude\t"):
            problems.append("`list --window day` did not print the expected header")
        elif len(listing) < 2:
            problems.append("`list --window day` printed no events")
        else:
            result["image"]["smoke_event_rows"] = len(listing) - 1

        log("==> docker-uid")
        name = f"{CONTAINER_PREFIX}-{self.run_id.lower()}-uid"
        uid_log = out / "docker-uid.log"
        codes["docker-uid"] = self.docker.run(
            "run",
            "--rm",
            "--name",
            name,
            "--entrypoint",
            "python",
            IMAGE_TAG,
            "-c",
            "import os; print(os.getuid())",
            log_path=uid_log,
        )
        (out / "docker-uid.exitcode").write_text(f"{codes['docker-uid']}\n", encoding="utf-8")
        uid = uid_log.read_text(encoding="utf-8").strip()
        result["image"]["runtime_uid"] = uid
        if not uid.isdigit() or int(uid) == 0:
            problems.append(f"container process runs as uid {uid!r}, expected non-root")
        log("    ok" if codes["docker-uid"] == 0 and not problems else "    FAILED")
        result["smoke_problems"] = problems
        return codes

    # -- orchestration -----------------------------------------------------------

    def execute(self) -> int:
        started_at = utc_stamp()
        started = time.monotonic()
        self.out.mkdir(parents=True)
        self.pip_cache.mkdir(parents=True, exist_ok=True)
        self.rootless = self.docker.is_rootless()
        log(f"commit under test : {self.sha} ({self.ref})")
        log(f"image list        : {self.images_path}")
        log(f"evidence directory: {self.out.relative_to(self.root)}")

        versions = [self.check_version(entry) for entry in self.images]
        build = self.build() if self.with_build else None
        if not self.keep_work:
            shutil.rmtree(self.work, ignore_errors=True)

        matrix_passed = all(item.get("passed") for item in versions)
        passed = matrix_passed and (build is None or bool(build.get("passed")))
        committed_images = subprocess.run(
            ["git", "-C", str(self.root), "show", f"{self.sha}:local-matrix-images.json"],
            check=False,
            capture_output=True,
        )
        images_sha = sha256_file(self.images_path)
        summary: dict[str, Any] = {
            "schema": 1,
            "git_sha": self.sha,
            "git_tree": self.tree,
            "git_ref": self.ref,
            "run_id": self.run_id,
            "started_at": started_at,
            "finished_at": utc_stamp(),
            "duration_seconds": round(time.monotonic() - started, 1),
            "source": "git archive of git_sha (clean export; working tree not used)",
            "images_file": {
                "path": str(self.images_path),
                "sha256": images_sha,
                "matches_tested_commit": committed_images.returncode == 0
                and hashlib.sha256(committed_images.stdout).hexdigest() == images_sha,
            },
            "docker": {
                "server_version": self.docker.output("version", "--format", "{{.Server.Version}}"),
                "rootless": self.rootless,
            },
            "isolation": {
                "mounts": [
                    "clean export -> /src",
                    "evidence dir -> /out",
                    ".cache/pip -> /pip-cache",
                ],
                "docker_cli_env": sorted(self.docker.env),
                "forbidden_names_checked": sorted(FORBIDDEN_NAMES),
            },
            "coverage_floor_percent": COVERAGE_FLOOR,
            "matrix": versions,
            "matrix_passed": matrix_passed,
            "build": build,
            "passed": passed,
        }
        (self.out / "summary.json").write_text(
            json.dumps(summary, indent=2) + "\n", encoding="utf-8"
        )
        (self.out / "SUMMARY.md").write_text(render_markdown(summary), encoding="utf-8")
        (self.out.parent / "LATEST").write_text(f"{self.run_id}\n", encoding="utf-8")

        log("\n" + render_markdown(summary))
        log(f"summary: {(self.out / 'summary.json').relative_to(self.root)}")
        return 0 if passed else 1


def render_markdown(summary: dict[str, Any]) -> str:
    def mark(code: int | None) -> str:
        return "n/a" if code is None else ("ok" if code == 0 else f"FAIL ({code})")

    lines = [
        f"# Local checks for `{summary['git_sha']}`",
        "",
        f"- Result: **{'PASS' if summary['passed'] else 'FAIL'}**",
        f"- Ref: `{summary['git_ref']}`  Run: `{summary['run_id']}`",
        f"- Started {summary['started_at']}, finished {summary['finished_at']}",
        "- Source: clean `git archive` export of the commit above",
        "",
        "| Python | Image digest | ruff check | ruff format | mypy --strict | unit | coverage "
        "| integration | result |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for item in summary["matrix"]:
        codes = item.get("exit_codes", {})
        coverage = item.get("coverage_percent")
        cells = [
            item.get("python_full_version") or item["python"],
            f"`{item['image'].split('@', 1)[-1][:19]}`",
            mark(codes.get("ruff-check")),
            mark(codes.get("ruff-format")),
            mark(codes.get("mypy")),
            mark(codes.get("unit")),
            "n/a" if coverage is None else f"{coverage:.2f}%",
            mark(codes["integration"]) if "integration" in codes else "not run",
            "PASS" if item.get("passed") else "FAIL",
        ]
        lines.append("| " + " | ".join(cells) + " |")
    build = summary.get("build")
    if build:
        lines += ["", f"## Build and smoke test: {'PASS' if build.get('passed') else 'FAIL'}", ""]
        for name, code in build.get("exit_codes", {}).items():
            lines.append(f"- {name}: {mark(code)}")
        image = build.get("image")
        if image:
            lines.append(
                f"- image `{image['tag']}` {image['id'][:19]}, "
                f"{image['size_bytes'] / 1e6:.0f} MB, user `{image['user']}`"
            )
        if build.get("artifacts"):
            lines += ["", "| Artifact | Bytes | SHA-256 |", "|---|---|---|"]
            lines += [
                f"| `{a['file']}` | {a['size']} | `{a['sha256']}` |" for a in build["artifacts"]
            ]
    problems = [
        f"- Python {i['python']}: {p}" for i in summary["matrix"] for p in i.get("problems", [])
    ]
    problems += [f"- Python {i['python']}: {i['error']}" for i in summary["matrix"] if "error" in i]
    problems += [f"- build: {p}" for p in (build or {}).get("problems", [])]
    if problems:
        lines += ["", "## Problems", "", *problems]
    return "\n".join(lines) + "\n"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run lint, mypy and tests for one commit in pinned Docker Python containers."
    )
    parser.add_argument("--ref", default="HEAD", help="commit, branch or tag to test [HEAD]")
    parser.add_argument(
        "--images", default=None, help="image list [local-matrix-images.json in the repo root]"
    )
    parser.add_argument(
        "--build",
        action="store_true",
        help=f"also build sdist/wheel and the {IMAGE_TAG} image, and smoke test them",
    )
    parser.add_argument(
        "--keep-work", action="store_true", help="keep the clean exports under .cache/local-checks"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        return Runner(args).execute()
    except CheckError as error:
        log(f"error: {error}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
