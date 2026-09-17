from kraft.worker_env import BASELINE, worker_env


def test_repo_scoped_leaks_never_reach_a_worker(monkeypatch):
    """The daemon's own VIRTUAL_ENV is why an activated shell exported one
    repo's venv to every worker (Kraft-69atv, Kraft-gxcmy)."""
    for leak in ("VIRTUAL_ENV", "PYTHONPATH", "UV_CACHE_DIR", "DIRENV_DIR"):
        monkeypatch.setenv(leak, "/somewhere/else")
    env = worker_env({})
    for leak in ("VIRTUAL_ENV", "PYTHONPATH", "UV_CACHE_DIR", "DIRENV_DIR"):
        assert leak not in env


def test_the_baseline_is_copied_from_the_daemon(monkeypatch):
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    assert worker_env({})["PATH"] == "/usr/bin:/bin"


def test_a_baseline_name_absent_from_the_daemon_is_simply_unset(monkeypatch):
    monkeypatch.delenv("SSH_AUTH_SOCK", raising=False)
    assert "SSH_AUTH_SOCK" not in worker_env({})


def test_the_repos_env_overrides_the_baseline(monkeypatch):
    monkeypatch.setenv("LANG", "C")
    assert worker_env({"env": {"LANG": "en_US.UTF-8"}})["LANG"] == "en_US.UTF-8"


def test_env_passthrough_carries_a_var_the_baseline_drops(monkeypatch):
    monkeypatch.setenv("MY_TOOL_HOME", "/opt/tool")
    assert "MY_TOOL_HOME" not in worker_env({})
    assert worker_env({"env_passthrough": ["MY_TOOL_HOME"]})["MY_TOOL_HOME"] == "/opt/tool"


def test_extra_wins_over_everything(monkeypatch):
    monkeypatch.setenv("LANG", "C")
    env = worker_env({"env": {"LANG": "en_US.UTF-8"}}, {"LANG": "POSIX"})
    assert env["LANG"] == "POSIX"


def test_forge_credentials_are_not_in_the_baseline(monkeypatch):
    """A worker does not push or merge; Kraft does. Kraft's own forge calls go
    through git.run_git, which this function never touches."""
    for token in ("GH_TOKEN", "GITHUB_TOKEN", "GITLAB_TOKEN"):
        monkeypatch.setenv(token, "secret")
        assert token not in worker_env({})
        assert token not in BASELINE


def test_a_none_repo_entry_still_produces_a_usable_baseline(monkeypatch):
    monkeypatch.setenv("PATH", "/usr/bin")
    assert worker_env(None)["PATH"] == "/usr/bin"
