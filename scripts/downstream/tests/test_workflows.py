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


def test_schedule_and_amd64_release_contract() -> None:
    workflow = yaml.load((WORKFLOWS / "downstream.yml").read_text(), Loader=yaml.BaseLoader)
    assert workflow["on"]["schedule"] == [{"cron": "26 18 * * *"}]
    assert workflow["concurrency"]["cancel-in-progress"] == "false"
    steps = workflow["jobs"]["release"]["steps"]
    builds = [step for step in steps if step.get("uses", "").startswith("docker/build-push-action@")]
    assert len(builds) == 1
    assert builds[0]["with"]["platforms"] == "linux/amd64"
    assert not [step for step in steps if step.get("uses", "").startswith("docker/setup-qemu-action@")]
    assert builds[0]["with"]["target"] == "standalone"
    assert "INCLUDE_LOCAL_MODELS=false" in builds[0]["with"]["build-args"]
    runtime = next(step for step in steps if step.get("name") == "Verify shipped code and amd64 runtime")
    assert "uv pip freeze" in runtime["run"]
    assert "--constraint /tmp/image-constraints.txt /source/hindsight-clients/python" in runtime["run"]


def test_downstream_uses_workflow_capable_credential_only_for_git_push() -> None:
    workflow = yaml.load((WORKFLOWS / "downstream.yml").read_text(), Loader=yaml.BaseLoader)
    steps = {step["name"]: step for step in workflow["jobs"]["release"]["steps"] if "name" in step}
    preflight = "Require workflow-capable push credential"
    build = "Build amd64 slim candidate"
    verify = "Sign and verify validated manifest"
    version = "Recheck release inputs and publish version"
    push = "Push validated source and release tag"
    publish = "Publish consumer tags and release notes"
    token = "${{ secrets.DOWNSTREAM_PUSH_TOKEN }}"
    builtin = "${{ secrets.GITHUB_TOKEN }}"
    assert [name for name, step in steps.items() if token in step.get("env", {}).values()] == [preflight, push]
    assert steps[push]["env"]["GH_TOKEN"] == token
    assert "credential.helper=!gh auth git-credential" in steps[push]["run"]
    assert 'push --atomic --force-with-lease="refs/heads/main:$OLD_MAIN"' in steps[push]["run"]
    assert steps[version]["env"]["GH_TOKEN"] == steps[publish]["env"]["GH_TOKEN"] == builtin
    assert steps["Log in to GHCR"]["with"]["password"] == builtin
    assert steps[preflight]["if"] == steps[push]["if"] == "steps.rebuild.outputs.changed == 'true'"
    names = list(steps)
    promotion = (preflight, build, "Verify shipped code and amd64 runtime", verify, version, push, publish)
    assert [name for name in names if name in promotion] == list(promotion)
    release_commands = [line for line in steps[publish]["run"].splitlines() if "gh release " in line]
    assert len(release_commands) == 2
    assert all('--repo "$GITHUB_REPOSITORY"' in line for line in release_commands)
