"""Exercise the actual rebuild script against local Git remotes, without publishing."""

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "rebuild.sh"


def git(path: Path, *args: str) -> str:
    return subprocess.check_output(
        [
            "git",
            "-C",
            str(path),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        text=True,
        stderr=subprocess.STDOUT,
    ).strip()


@dataclass
class Repo:
    checkout: Path
    origin: Path
    upstream: Path
    output: Path

    def rebuild(self, number: int = 1, *, force: bool = False) -> subprocess.CompletedProcess[str]:
        self.output.write_text("")
        return subprocess.run(
            [shutil.which("bash"), str(SCRIPT)],
            cwd=self.checkout,
            env={
                **os.environ,
                "PYTHON": Path(sys.executable).as_posix(),
                "GITHUB_ACTIONS": "true",
                "GITHUB_OUTPUT": self.output.as_posix(),
                "GITHUB_RUN_NUMBER": str(number),
                "GITHUB_REPOSITORY_OWNER": "JohnsonRan",
                "FORCE_REBUILD": "true" if force else "false",
            },
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    source = tmp_path / "source"
    source.mkdir()
    git(source, "init", "--initial-branch=main")
    (source / "hindsight-api-slim").mkdir()
    (source / "hindsight-api-slim/pyproject.toml").write_text('[project]\nversion="1.2.1"\n')
    (source / "README.md").write_text("upstream readme\n")
    (source / "code.txt").write_text("base\n")
    git(source, "add", ".")
    git(source, "commit", "-m", "base")
    upstream = tmp_path / "upstream.git"
    origin = tmp_path / "origin.git"
    git(tmp_path, "clone", "--bare", str(source), str(upstream))
    git(source, "switch", "-c", "ci")
    (source / "README.md").write_text("downstream readme\n")
    (source / "maintenance.txt").write_text("ci overlay\n")
    git(source, "add", ".")
    git(source, "commit", "-m", "ci overlay")
    git(source, "switch", "-c", "feat/openai-streaming", "main")
    (source / "code.txt").write_text("streaming patch\n")
    git(source, "add", ".")
    git(source, "commit", "-m", "streaming")
    git(tmp_path, "clone", "--bare", str(source), str(origin))
    checkout = tmp_path / "checkout"
    git(tmp_path, "clone", "--branch", "ci", str(origin), str(checkout))
    git(checkout, "remote", "add", "upstream", str(upstream))
    return Repo(checkout, origin, upstream, tmp_path / "outputs")


def test_candidate_has_pinned_inputs_and_countable_integrations(repo: Repo) -> None:
    result = repo.rebuild()
    assert result.returncode == 0, result.stdout
    metadata = json.loads((repo.checkout / ".downstream/provenance.json").read_text())
    assert metadata["inputs"] == {
        "upstream": git(repo.upstream, "rev-parse", "main"),
        "ci": git(repo.origin, "rev-parse", "ci"),
        "patches": [{"branch": "feat/openai-streaming", "sha": git(repo.origin, "rev-parse", "feat/openai-streaming")}],
    }
    assert metadata["version"] == "1.2.1-fork.1-slim"
    assert (repo.checkout / "code.txt").read_text() == "streaming patch\n"
    assert (repo.checkout / "README.md").read_text() == "downstream readme\n"
    assert git(repo.checkout, "rev-list", "--count", "upstream/main..HEAD") == "2"
    assert "changed=true" in repo.output.read_text()
    # A local rebuild must never publish main as a side effect.
    assert git(repo.origin, "rev-parse", "main") == metadata["inputs"]["upstream"]


def test_unchanged_inputs_are_skipped_and_unknown_main_changes_are_refused(repo: Repo) -> None:
    first = repo.rebuild()
    assert first.returncode == 0, first.stdout
    metadata = json.loads((repo.checkout / ".downstream/provenance.json").read_text())
    git(repo.checkout, "push", "origin", "HEAD:main", f"HEAD:refs/tags/{metadata['release_tag']}")
    git(repo.checkout, "switch", "ci")
    second = repo.rebuild(2)
    assert second.returncode == 0, second.stdout
    assert "changed=false" in repo.output.read_text()
    forced = repo.rebuild(3, force=True)
    assert forced.returncode == 0, forced.stdout
    assert "changed=true" in repo.output.read_text()
    git(repo.checkout, "switch", "--detach", "origin/main")
    (repo.checkout / "unknown.txt").write_text("unrecorded operator change\n")
    git(repo.checkout, "add", ".")
    git(repo.checkout, "commit", "-m", "unknown update")
    git(repo.checkout, "push", "origin", "HEAD:main")
    git(repo.checkout, "switch", "ci")
    third = repo.rebuild(3)
    assert third.returncode != 0
    assert "unknown update" in third.stdout


@pytest.mark.parametrize("dirty", ["tracked", "untracked"])
def test_dirty_worktree_is_refused(repo: Repo, dirty: str) -> None:
    (repo.checkout / ("code.txt" if dirty == "tracked" else "untracked.txt")).write_text("do not discard me\n")
    assert repo.rebuild().returncode != 0
    assert (
        repo.checkout / ("code.txt" if dirty == "tracked" else "untracked.txt")
    ).read_text() == "do not discard me\n"


def test_empty_patch_is_not_silently_retired(repo: Repo) -> None:
    git(repo.checkout, "push", str(repo.upstream), "origin/feat/openai-streaming:refs/heads/main")
    result = repo.rebuild()
    assert result.returncode != 0
    assert "is empty" in result.stdout


def test_conflicting_upstream_stops_without_updating_main(repo: Repo, tmp_path: Path) -> None:
    upstream_checkout = tmp_path / "upstream-edit"
    git(tmp_path, "clone", str(repo.upstream), str(upstream_checkout))
    (upstream_checkout / "code.txt").write_text("upstream incompatible change\n")
    git(upstream_checkout, "add", ".")
    git(upstream_checkout, "commit", "-m", "upstream changed same line")
    git(upstream_checkout, "push", "origin", "main")
    original_main = git(repo.origin, "rev-parse", "main")
    result = repo.rebuild()
    assert result.returncode != 0
    assert "CONFLICT" in result.stdout
    assert git(repo.origin, "rev-parse", "main") == original_main
