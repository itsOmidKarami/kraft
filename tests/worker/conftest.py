"""Fixtures for the tests that run a sandboxed worker in a real container."""

import pytest
from support.sandbox_image import build_git_image


@pytest.fixture(
    params=[
        pytest.param("docker", marks=pytest.mark.e2e("docker")),
        pytest.param("podman", marks=pytest.mark.e2e("podman")),
    ]
)
def git_image(request, tmp_path, monkeypatch):
    return build_git_image(request.param, tmp_path, monkeypatch)
