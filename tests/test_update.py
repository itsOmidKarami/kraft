"""`kraft.update` - the version check.

Its three callers (admin health, admin doctor, admin start) all sit on paths a
disconnected machine must still walk, so the contract under test is mostly
negative: no failure mode raises, and no failure mode reports an update.
"""

from __future__ import annotations

import json
import pathlib

import httpx
import pytest

from kraft import update

RELEASE_JSON = [
    {
        "tag_name": "v0.4.0",
        "draft": False,
        "prerelease": False,
        "assets": [
            {
                "name": "kraft-0.4.0-py3-none-any.whl",
                "browser_download_url": "https://x/w.whl",
            }
        ],
    }
]


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setenv("KRAFT_HOME", str(tmp_path))
    return tmp_path / "run" / "update-check.json"


def _fetch(payload):
    def fetch(_url, _timeout):
        return payload

    return fetch


def test_latest_reads_the_newest_release(cache, monkeypatch):
    monkeypatch.setattr(update, "_fetch", _fetch(RELEASE_JSON))
    release = update.latest()
    assert release.tag == "v0.4.0"
    assert release.wheel_url == "https://x/w.whl"


def test_latest_caches_to_disk(cache, monkeypatch):
    monkeypatch.setattr(update, "_fetch", _fetch(RELEASE_JSON))
    update.latest()
    assert json.loads(cache.read_text())["tag"] == "v0.4.0"


def test_a_cache_hit_does_not_fetch(cache, monkeypatch):
    monkeypatch.setattr(update, "_fetch", _fetch(RELEASE_JSON))
    update.latest()

    def explode(_url, _timeout):
        raise AssertionError("fetched inside the TTL")

    monkeypatch.setattr(update, "_fetch", explode)
    assert update.latest().tag == "v0.4.0"


def test_an_expired_cache_refetches(cache, monkeypatch):
    monkeypatch.setattr(update, "_fetch", _fetch(RELEASE_JSON))
    update.latest()
    stale = json.loads(cache.read_text())
    stale["checked_at"] = 0
    cache.write_text(json.dumps(stale))
    newer = [
        {
            "tag_name": "v0.5.0",
            "draft": False,
            "prerelease": False,
            "assets": [{"name": "k.whl", "browser_download_url": "https://x/n.whl"}],
        }
    ]
    monkeypatch.setattr(update, "_fetch", _fetch(newer))
    assert update.latest().tag == "v0.5.0"


@pytest.mark.parametrize(
    "boom",
    [
        httpx.ConnectError("no network"),
        httpx.ReadTimeout("too slow"),
        ValueError("not json"),
    ],
)
def test_every_transport_failure_is_none_not_an_exception(cache, monkeypatch, boom):
    def fetch(_url, _timeout):
        raise boom

    monkeypatch.setattr(update, "_fetch", fetch)
    assert update.latest() is None


def test_a_garbage_body_is_none(cache, monkeypatch):
    monkeypatch.setattr(update, "_fetch", _fetch({"not": "a list"}))
    assert update.latest() is None


def test_an_empty_release_list_is_none(cache, monkeypatch):
    monkeypatch.setattr(update, "_fetch", _fetch([]))
    assert update.latest() is None


def test_a_release_with_no_wheel_is_none(cache, monkeypatch):
    payload = [{"tag_name": "v9.0.0", "draft": False, "prerelease": False, "assets": []}]
    monkeypatch.setattr(update, "_fetch", _fetch(payload))
    assert update.latest() is None


def test_a_draft_release_is_not_an_update(cache, monkeypatch):
    """GitHub lists drafts in the same feed; GitLab had no equivalent."""
    payload = [
        {
            "tag_name": "v0.9.9",
            "draft": True,
            "prerelease": False,
            "assets": [{"name": "k.whl", "browser_download_url": "https://x/d.whl"}],
        },
        *RELEASE_JSON,
    ]
    monkeypatch.setattr(update, "_fetch", _fetch(payload))
    assert update.latest().tag == "v0.4.0"


def test_a_prerelease_is_not_an_update(cache, monkeypatch):
    payload = [
        {
            "tag_name": "v1.0.0rc1",
            "draft": False,
            "prerelease": True,
            "assets": [{"name": "k.whl", "browser_download_url": "https://x/p.whl"}],
        },
        *RELEASE_JSON,
    ]
    monkeypatch.setattr(update, "_fetch", _fetch(payload))
    assert update.latest().tag == "v0.4.0"


def test_a_release_with_no_wheel_is_skipped(cache, monkeypatch):
    payload = [
        {"tag_name": "v0.9.9", "draft": False, "prerelease": False, "assets": []},
        *RELEASE_JSON,
    ]
    monkeypatch.setattr(update, "_fetch", _fetch(payload))
    assert update.latest().tag == "v0.4.0"


@pytest.mark.parametrize(
    ("here", "there", "behind"),
    [
        ("0.3.0", "v0.4.0", True),
        ("0.4.0", "v0.4.0", False),
        ("0.5.0", "v0.4.0", False),
        ("0.3.1.dev4+g1a2b3c", "v0.3.0", False),
        ("0.3.1.dev4+g1a2b3c", "v0.4.0", True),
        ("0.0.0+source", "v0.4.0", True),
        ("0.4.0", "vnonsense", False),
    ],
)
def test_is_behind_compares_versions(monkeypatch, here, there, behind):
    monkeypatch.setattr(update, "installed", lambda: here)
    assert update.is_behind(update.Release(tag=there, wheel_url="u")) is behind


def test_is_behind_of_nothing_is_false():
    assert update.is_behind(None) is False


def test_perform_downloads_the_wheel_and_installs_the_local_copy(monkeypatch):
    monkeypatch.setattr(update, "_request", lambda _url, _timeout: b"WHEEL BYTES")
    seen = {}

    def run(command, **kwargs):
        seen["command"] = command
        wheel_path = pathlib.Path(command[5])
        seen["wheel_bytes"] = wheel_path.read_bytes()
        return type("R", (), {"returncode": 0})()

    code = update.perform(update.Release(tag="v0.4.0", wheel_url="https://x/w.whl"), run=run)
    assert code == 0
    command = seen["command"]
    assert command[:5] == ["uv", "tool", "install", "--force", "--from"]
    assert command[6] == "kraft-sdlc"
    assert pathlib.Path(command[5]).name == "w.whl"
    assert seen["wheel_bytes"] == b"WHEEL BYTES"


def test_perform_installs_under_the_pyproject_package_name(monkeypatch):
    """A future PyPI rename that misses this call site should fail loudly, not silently."""
    import tomllib

    monkeypatch.setattr(update, "_request", lambda _url, _timeout: b"")
    seen = {}

    def run(command, **_kwargs):
        seen["command"] = command
        return type("R", (), {"returncode": 0})()

    update.perform(update.Release(tag="v0.4.0", wheel_url="u/w.whl"), run=run)

    repo_root = pathlib.Path(__file__).parent.parent
    package_name = tomllib.loads((repo_root / "pyproject.toml").read_text())["project"]["name"]
    assert seen["command"][-1] == package_name


def test_just_install_uses_the_pyproject_package_name():
    import tomllib

    repo_root = pathlib.Path(__file__).parent.parent
    package_name = tomllib.loads((repo_root / "pyproject.toml").read_text())["project"]["name"]
    lines = (repo_root / "justfile").read_text().splitlines()
    install_line = next(line for line in lines if "uv tool install" in line)
    assert package_name in install_line.split()


def test_perform_downloads_with_the_long_timeout_not_the_check_timeout(monkeypatch):
    seen = {}

    def request(_url, timeout):
        seen["timeout"] = timeout
        return b""

    monkeypatch.setattr(update, "_request", request)
    run = lambda _command, **_kwargs: type("R", (), {"returncode": 0})()  # noqa: E731
    update.perform(update.Release(tag="v0.4.0", wheel_url="u/w.whl"), run=run)
    assert seen["timeout"] == update.DOWNLOAD_TIMEOUT > update.TIMEOUT


def test_perform_reports_a_failing_installer(monkeypatch):
    monkeypatch.setattr(update, "_request", lambda _url, _timeout: b"")

    def run(_command, **_kwargs):
        return type("R", (), {"returncode": 2})()

    assert update.perform(update.Release(tag="v0.4.0", wheel_url="u/w.whl"), run=run) == 2


def test_perform_without_uv_is_a_readable_failure(monkeypatch):
    monkeypatch.setattr(update, "_request", lambda _url, _timeout: b"")

    def run(_command, **_kwargs):
        raise FileNotFoundError("uv")

    with pytest.raises(SystemExit, match="uv"):
        update.perform(update.Release(tag="v0.4.0", wheel_url="u/w.whl"), run=run)


def test_perform_under_homebrew_calls_brew_upgrade_not_uv(monkeypatch):
    monkeypatch.setattr(update.sys, "prefix", "/opt/homebrew/Cellar/kraft/0.65.0/libexec")

    def request(_url, _timeout):
        raise AssertionError("a homebrew install must not download the wheel itself")

    monkeypatch.setattr(update, "_request", request)
    seen = {}

    def run(command, **_kwargs):
        seen["command"] = command
        return type("R", (), {"returncode": 0})()

    code = update.perform(update.Release(tag="v0.4.0", wheel_url="https://x/w.whl"), run=run)
    assert code == 0
    assert seen["command"] == ["brew", "upgrade", "kraft"]


def test_perform_under_homebrew_without_brew_is_a_readable_failure(monkeypatch):
    monkeypatch.setattr(update.sys, "prefix", "/opt/homebrew/Cellar/kraft/0.65.0/libexec")
    monkeypatch.setattr(update, "_request", lambda _url, _timeout: b"")

    def run(_command, **_kwargs):
        raise FileNotFoundError("brew")

    with pytest.raises(SystemExit, match="brew"):
        update.perform(update.Release(tag="v0.4.0", wheel_url="u/w.whl"), run=run)


def test_is_homebrew_install_is_false_for_a_uv_tool_prefix(monkeypatch):
    monkeypatch.setattr(update.sys, "prefix", "/Users/x/.local/share/uv/tools/kraft-sdlc")
    assert update._is_homebrew_install() is False
