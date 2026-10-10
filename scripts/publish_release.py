#!/usr/bin/env python3
"""Publish locally built distributions as GitHub Release assets - gated and verified.

Nothing is uploaded unless all of these hold for the tag's commit:

* ``scripts/local_checks.py --build`` recorded a passing run for exactly that
  commit (full matrix, build, wheel smoke test and Docker image smoke test);
* the sdist and wheel on disk still hash to what that run recorded;
* the tag exists on the remote and points at the same commit.

After ``gh release create`` the assets are downloaded again anonymously from
their public URLs and their SHA-256 compared with the local files. The result
is written next to the evidence as ``release-verification.json``.

Authentication: ``gh`` reads ``GH_TOKEN`` from the environment. Alternatively
pass ``--token-file``; the token is then read at run time and handed to ``gh``
through its environment only - never on a command line, never saved to
gh/git configuration.

Usage: python scripts/publish_release.py --tag v0.1.0 --repo OWNER/NAME
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class ReleaseError(RuntimeError):
    """A gate failed or a GitHub operation did not behave as required."""


def log(message: str) -> None:
    print(message, flush=True)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args], check=False, capture_output=True, text=True
    )
    if result.returncode != 0:
        raise ReleaseError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def gh_environment(root: Path, token_file: str | None) -> dict[str, str]:
    """Environment for ``gh``: token via env only, config kept inside the workspace."""
    env = dict(os.environ)
    if token_file:
        env["GH_TOKEN"] = Path(token_file).read_text(encoding="utf-8").strip()
    if not env.get("GH_TOKEN") and not env.get("GITHUB_TOKEN"):
        raise ReleaseError("no GitHub token: set GH_TOKEN or pass --token-file")
    config_dir = root / ".cache" / "gh-config"
    config_dir.mkdir(parents=True, exist_ok=True)
    env["GH_CONFIG_DIR"] = str(config_dir)
    env["GH_PROMPT_DISABLED"] = "1"
    env["GH_NO_UPDATE_NOTIFIER"] = "1"
    return env


def gh(env: dict[str, str], *args: str) -> str:
    result = subprocess.run(["gh", *args], env=env, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        raise ReleaseError(f"gh {' '.join(args[:3])} ... failed: {result.stderr.strip()}")
    return result.stdout


def find_passing_run(root: Path, sha: str, run_id: str | None) -> tuple[Path, dict[str, Any]]:
    """The newest (or the named) evidence run for ``sha`` that passed matrix + build."""
    base = root / "artifacts" / "local-checks" / sha
    candidates = [base / run_id] if run_id else sorted(base.glob("*/"), reverse=True)
    reasons: list[str] = []
    for directory in candidates:
        summary_path = directory / "summary.json"
        if not summary_path.is_file():
            reasons.append(f"{directory.name}: no summary.json")
            continue
        summary: dict[str, Any] = json.loads(summary_path.read_text(encoding="utf-8"))
        build = summary.get("build") or {}
        if summary.get("git_sha") != sha:
            reasons.append(f"{directory.name}: evidence is for {summary.get('git_sha')}")
        elif not summary.get("matrix_passed"):
            reasons.append(f"{directory.name}: matrix did not pass")
        elif not build.get("passed"):
            reasons.append(f"{directory.name}: build/smoke stage missing or failed")
        elif not summary.get("passed"):
            reasons.append(f"{directory.name}: run did not pass")
        else:
            return directory, summary
    detail = "; ".join(reasons) or f"no runs under {base.relative_to(root)}"
    raise ReleaseError(
        f"no passing `local_checks.py --build` evidence for {sha} ({detail}). "
        f"Run: python scripts/local_checks.py --ref {sha} --build"
    )


def check_local_artifacts(root: Path, summary: dict[str, Any]) -> list[dict[str, Any]]:
    """Re-hash the built files and insist they still match the recorded evidence."""
    artifacts: list[dict[str, Any]] = summary["build"]["artifacts"]
    kinds = sorted("sdist" if a["file"].endswith(".tar.gz") else "wheel" for a in artifacts)
    if kinds != ["sdist", "wheel"]:
        raise ReleaseError(f"expected exactly one sdist and one wheel, evidence lists {kinds}")
    for artifact in artifacts:
        path = root / artifact["path"]
        if not path.is_file():
            raise ReleaseError(f"{artifact['path']} is missing")
        actual = sha256_bytes(path.read_bytes())
        if actual != artifact["sha256"]:
            raise ReleaseError(
                f"{artifact['path']} changed since it was tested: "
                f"sha256 {actual}, evidence says {artifact['sha256']}"
            )
    return artifacts


def remote_tag_sha(env: dict[str, str], repo: str, tag: str) -> str:
    """The commit a remote tag points at (peeling annotated tags)."""
    ref = json.loads(gh(env, "api", f"repos/{repo}/git/ref/tags/{tag}"))
    target = ref["object"]
    if target["type"] == "tag":
        target = json.loads(gh(env, "api", f"repos/{repo}/git/tags/{target['sha']}"))["object"]
    if target["type"] != "commit":
        raise ReleaseError(f"remote tag {tag} points at a {target['type']}, not a commit")
    return str(target["sha"])


def download(url: str) -> bytes:
    if not url.startswith("https://github.com/"):
        raise ReleaseError(f"unexpected asset URL {url}")
    request = urllib.request.Request(url, headers={"User-Agent": "quakewatch-release-verifier"})  # noqa: S310
    with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310
        data: bytes = response.read()
    return data


def verify_release(
    env: dict[str, str], repo: str, tag: str, artifacts: list[dict[str, Any]], save_to: Path
) -> dict[str, Any]:
    """Fetch the published assets anonymously and compare them with the local builds."""
    release = json.loads(gh(env, "api", f"repos/{repo}/releases/tags/{tag}"))
    remote = {asset["name"]: asset for asset in release["assets"]}
    expected = {artifact["file"]: artifact for artifact in artifacts}
    problems: list[str] = []
    if set(remote) != set(expected):
        problems.append(f"release assets {sorted(remote)} != local artifacts {sorted(expected)}")
    save_to.mkdir(parents=True, exist_ok=True)
    checked: list[dict[str, Any]] = []
    for name, artifact in sorted(expected.items()):
        asset = remote.get(name)
        if asset is None:
            continue
        data = download(asset["browser_download_url"])
        (save_to / name).write_bytes(data)
        downloaded = sha256_bytes(data)
        api_digest = asset.get("digest")
        entry = {
            "file": name,
            "url": asset["browser_download_url"],
            "local_sha256": artifact["sha256"],
            "downloaded_sha256": downloaded,
            "github_api_digest": api_digest,
            "local_size": artifact["size"],
            "downloaded_size": len(data),
            "github_api_size": asset["size"],
            "match": downloaded == artifact["sha256"] and len(data) == artifact["size"],
        }
        if not entry["match"]:
            problems.append(f"{name}: downloaded bytes differ from the local build")
        if api_digest and api_digest != f"sha256:{artifact['sha256']}":
            problems.append(f"{name}: GitHub reports digest {api_digest}")
        if asset["size"] != artifact["size"]:
            problems.append(f"{name}: GitHub reports {asset['size']} bytes")
        checked.append(entry)
    return {
        "release_url": release["html_url"],
        "tag": release["tag_name"],
        "draft": release["draft"],
        "prerelease": release["prerelease"],
        "asset_count": len(remote),
        "assets": checked,
        "downloads_saved_to": str(save_to),
        "problems": problems,
        "verified": not problems,
    }


def release_notes(tag: str, sha: str, summary: dict[str, Any], evidence: Path, root: Path) -> str:
    rows = "\n".join(
        f"| `{a['file']}` | {a['size']} | `{a['sha256']}` |" for a in summary["build"]["artifacts"]
    )
    matrix = "\n".join(
        f"- Python {item['python_full_version']} (`{item['image']}`): ruff, mypy --strict, "
        f"unit tests with {item['coverage_percent']:.2f}% coverage"
        + (", live integration tests" if item["integration"] == "ran" else "")
        for item in summary["matrix"]
    )
    return (
        f"quakewatch {tag.lstrip('v')}\n\n"
        f"Built locally from commit `{sha}` and published only after the local Docker check "
        f"matrix and smoke tests passed for that exact commit "
        f"(run `{summary['run_id']}`, evidence in `{evidence.relative_to(root)}`).\n\n"
        f"### Verification\n\n{matrix}\n"
        f"- sdist and wheel built in the Python 3.12 container; wheel installed into a clean "
        f"virtualenv and smoke tested\n"
        f"- `agentbench-quakewatch:local` image built and `list --window day` smoke tested\n\n"
        f"### SHA-256\n\n| Asset | Bytes | SHA-256 |\n|---|---|---|\n{rows}\n"
    )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n", maxsplit=1)[0])
    parser.add_argument("--tag", required=True, help="existing tag to release, e.g. v0.1.0")
    parser.add_argument("--repo", required=True, help="GitHub repository as OWNER/NAME")
    parser.add_argument("--run-id", default=None, help="evidence run to use [newest passing]")
    parser.add_argument("--token-file", default=None, help="file holding the GitHub token")
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="do not create anything; re-verify the already published release",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        root = Path(git(Path.cwd(), "rev-parse", "--show-toplevel"))
        sha = git(root, "rev-parse", "--verify", f"refs/tags/{args.tag}^{{commit}}")
        evidence, summary = find_passing_run(root, sha, args.run_id)
        artifacts = check_local_artifacts(root, summary)
        env = gh_environment(root, args.token_file)
        remote_sha = remote_tag_sha(env, args.repo, args.tag)
        if remote_sha != sha:
            raise ReleaseError(f"remote tag {args.tag} is {remote_sha}, local tag is {sha}")
        log(f"tag {args.tag} -> {sha} (local and remote agree)")
        log(f"evidence: {evidence.relative_to(root)} (matrix + build + smoke passed)")

        if not args.verify_only:
            notes = evidence / "release-notes.md"
            notes.write_text(
                release_notes(args.tag, sha, summary, evidence, root), encoding="utf-8"
            )
            url = gh(
                env,
                "release",
                "create",
                args.tag,
                *[str(root / artifact["path"]) for artifact in artifacts],
                "--repo",
                args.repo,
                "--verify-tag",
                "--title",
                f"quakewatch {args.tag.lstrip('v')}",
                "--notes-file",
                str(notes),
            ).strip()
            log(f"created release: {url}")

        report = verify_release(env, args.repo, args.tag, artifacts, evidence / "release-download")
        report.update(
            {
                "git_sha": sha,
                "repo": args.repo,
                "evidence_run": summary["run_id"],
                "verified_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
        )
        report_path = evidence / "release-verification.json"
        report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        for asset in report["assets"]:
            log(f"  {asset['file']}: sha256 {asset['downloaded_sha256']} match={asset['match']}")
        log(f"report: {report_path.relative_to(root)}")
        if not report["verified"]:
            raise ReleaseError("; ".join(report["problems"]))
        log(f"release verified: {report['release_url']}")
    except ReleaseError as error:
        log(f"error: {error}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
