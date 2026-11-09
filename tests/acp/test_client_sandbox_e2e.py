"""Kraft's ACP client over `docker run -i`: the one proof that a real container carries the
conversation, and that the client's gate address stays out of it (ACP part 3)."""

import json
import os
import subprocess

import pytest
from support.acp import FAKE, events, run_client
from support.sandbox_image import build_git_image

from kraft.worker.backends import docker

pytestmark = pytest.mark.e2e("docker")


@pytest.fixture
def python_image(tmp_path, monkeypatch):
    return build_git_image("docker", tmp_path, monkeypatch, python=True)


def test_an_acp_conversation_runs_through_docker_run_i(tmp_path, python_image):
    result = tmp_path / "result.json"
    result.touch()  # as `run_task` does: docker binds only a file that exists
    wrapped = docker.docker_argv(
        ["python3", str(FAKE)],
        tmp_path,
        {"kind": "docker", "image": python_image},
        None,
        interactive=True,
        ro_paths=[FAKE],
        result_path=result,
        rw_files=[result],
        env={"KRAFT_FAKE_ACP": ""},
    )
    done, records = run_client(tmp_path, agent_argv=wrapped)
    assert done.returncode == 0, done.stdout + done.stderr
    assert events(records, "stop")[0]["stop_reason"] == "end_turn"
    assert (
        json.loads(result.read_text())["status"] == "done"
    )  # what the fake wrote in the container


def test_the_clients_gate_address_is_not_in_a_real_containers_environment(tmp_path, python_image):
    argv = docker.docker_argv(
        ["sh", "-c", "env"], tmp_path, {"kind": "docker", "image": python_image}, None
    )
    client_env = {
        **os.environ,
        "KRAFT_HOST": "127.0.0.1",
        "KRAFT_PORT": "424242-canary",
        "KRAFT_RESULT_PATH": "/x/result.json",
    }
    ran = subprocess.run(argv, capture_output=True, text=True, env=client_env)
    assert ran.returncode == 0, ran.stderr
    assert "424242-canary" not in ran.stdout and "KRAFT_PORT" not in ran.stdout
    assert "KRAFT_RESULT_PATH=/x/result.json" in ran.stdout  # a name that does cross, so this reads
