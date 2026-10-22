"""`python -m kraft.start_check`: can the Kraft installed now start."""

from __future__ import annotations

import subprocess
import sys

import pytest

from kraft import start_check


@pytest.fixture
def config(tmp_path, monkeypatch):
    directory = tmp_path / "config"
    directory.mkdir()
    monkeypatch.setenv("KRAFT_CONFIG_DIR", str(directory))
    monkeypatch.delenv("KRAFT_HOST", raising=False)
    monkeypatch.delenv("KRAFT_PORT", raising=False)
    return directory


@pytest.mark.parametrize(
    ("access", "code", "says"),
    [
        pytest.param(None, 0, "", id="ok"),
        pytest.param("port: 8080\n", 0, "", id="a-port"),
        pytest.param("port: not-a-port\n", 1, "access.yaml", id="bad-port-in-access-yaml"),
        pytest.param("port: 70000\n", 1, "70000", id="port-out-of-range"),
        pytest.param("bind: 'no spaces allowed'\n", 1, "bind", id="bad-bind"),
        pytest.param("bind: 0.0.0.0\n", 1, "password", id="lan-bind-without-a-password"),
    ],
)
def test_start_check(config, capsys, access, code, says):
    if access:
        (config / "access.yaml").write_text(access)
    before = sorted(p.name for p in config.iterdir())

    assert start_check.main() == code

    assert says in capsys.readouterr().err
    assert sorted(p.name for p in config.iterdir()) == before  # it writes nothing


def test_a_settings_file_that_does_not_load_stops_it(config, capsys):
    (config / "update.yaml").write_text("allowed_hours: ['1-6']\n")
    assert start_check.main() == 1
    assert "update.yaml" in capsys.readouterr().err


def test_it_runs_as_a_module_with_the_tools_own_interpreter(config):
    done = subprocess.run(
        [sys.executable, "-P", "-m", "kraft.start_check"], capture_output=True, text=True
    )
    assert (done.returncode, done.stderr) == (0, "")
