"""Guard the whole publisher family, not just a single job accidentally left enabled."""

from pathlib import Path

import pytest
import yaml

WORKFLOWS = Path(__file__).resolve().parents[3] / ".github/workflows"


@pytest.mark.parametrize("filename", ["release.yml", "release-integration.yml"])
def test_every_upstream_publish_job_is_guarded(filename: str) -> None:
    workflow = yaml.load((WORKFLOWS / filename).read_text(), Loader=yaml.BaseLoader)
    for name, job in workflow["jobs"].items():
        assert job.get("if") == "github.repository == 'vectorize-io/hindsight'", name


def test_schedule_and_dual_architecture_release_contract() -> None:
    workflow = yaml.load((WORKFLOWS / "downstream.yml").read_text(), Loader=yaml.BaseLoader)
    assert workflow["on"]["schedule"] == [{"cron": "26 18 * * *"}]
    assert workflow["concurrency"]["cancel-in-progress"] == "false"
    steps = workflow["jobs"]["release"]["steps"]
    builds = [step for step in steps if step.get("uses", "").startswith("docker/build-push-action@")]
    assert len(builds) == 1
    assert builds[0]["with"]["platforms"] == "linux/amd64,linux/arm64"
    assert builds[0]["with"]["target"] == "standalone"
    assert "INCLUDE_LOCAL_MODELS=false" in builds[0]["with"]["build-args"]
    runtime = next(step for step in steps if step.get("name") == "Verify shipped code and both platform runtimes")
    assert "uv pip freeze" in runtime["run"]
    assert "--constraint /tmp/image-constraints.txt /source/hindsight-clients/python" in runtime["run"]
    publish = next(step for step in steps if step.get("name") == "Publish validated source and consumer tags")
    release_commands = [line for line in publish["run"].splitlines() if "gh release " in line]
    assert len(release_commands) == 2
    assert all('--repo "$GITHUB_REPOSITORY"' in line for line in release_commands)
    names = [step.get("name", "") for step in steps]
    assert names.index("Verify shipped code and both platform runtimes") < names.index(
        "Publish validated source and consumer tags"
    )
