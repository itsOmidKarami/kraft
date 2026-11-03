"""The update preview: the built-in a release wheel ships, read before install."""

from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

import pytest

from kraft.addons import builtin, fetch

PREFIX = "kraft/_bundled/kraft-default/"


def _wheel(path: Path, members: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        for name, data in members.items():
            z.writestr(name, data)
    return path


@pytest.mark.parametrize(
    "members, expect",
    [
        pytest.param(
            {PREFIX + ".kraft/addon.json": b"{}", PREFIX + "chains/default.yaml": b"id: default\n"},
            "files",
            id="has-builtin",
        ),
        pytest.param({"kraft/__init__.py": b""}, None, id="older-release"),
        pytest.param({PREFIX + "../../evil": b"x"}, ValueError, id="path-escape"),
        pytest.param(
            {PREFIX + f"chains/{n}.yaml": b"" for n in range(fetch.MAX_FILES + 1)},
            ValueError,
            id="too-many-files",
        ),
        pytest.param(
            {PREFIX + f"chains/{n}.yaml": b"0" * fetch.MAX_YAML for n in range(21)},
            ValueError,
            id="too-many-bytes",
        ),
    ],
)
def test_from_wheel(tmp_path: Path, members, expect) -> None:
    wheel = _wheel(tmp_path / "k.whl", members)
    scratch = tmp_path / "scratch"
    if expect is ValueError:
        with pytest.raises(ValueError):
            builtin.from_wheel(wheel, scratch)
        return
    found = builtin.from_wheel(wheel, scratch)
    if expect is None:
        assert found is None
    else:
        assert {".kraft/addon.json", "chains/default.yaml"} <= set(found.files)


def _release_wheel(tmp_path: Path, drop: str | None = None, rename_task: bool = False) -> Path:
    """A wheel shipping the repo's built-in, optionally with quick-task's node
    `verify` renamed to `drop`, or default's `spec` task `author` renamed."""
    repo = Path(__file__).resolve().parents[2] / "config"
    members = {}
    for sub in ("chains", ".kraft", "library.yaml"):
        src = repo / sub
        files = [src] if src.is_file() else [p for p in src.rglob("*") if p.is_file()]
        for p in files:
            data = p.read_bytes()
            if drop and p.name == "quick-task.yaml":
                data = data.replace(b"id: verify\n", f"id: {drop}\n".encode())
            if rename_task and p.name == "default.yaml":
                data = data.replace(
                    b"      - id: author\n        extends: spec_author",
                    b"      - id: writer\n        extends: spec_author",
                )
            members[PREFIX + str(p.relative_to(repo))] = data
    return _wheel(tmp_path / "new.whl", members)


@pytest.mark.parametrize(
    "mode, says",
    [
        pytest.param("unchanged", None, id="unchanged"),
        pytest.param("renamed-node", "verify", id="a-chain-changes"),
        pytest.param("breaks-override", "chains/default.yaml", id="breaks-an-override"),
        pytest.param("offline", "cannot compare", id="offline"),
        pytest.param("broken-policy", "cannot compare", id="broken-policy"),
        pytest.param("no-url", "cannot compare: no wheel found", id="no-wheel-found"),
        pytest.param("older", "ships no built-in kraft-default", id="older-release"),
    ],
)
def test_preview(tmp_path: Path, monkeypatch, mode, says) -> None:
    import httpx
    from support.addons import home

    from kraft import update

    config = home(tmp_path)
    if mode == "breaks-override":
        (config / "chains").mkdir(exist_ok=True)
        (config / "chains" / "default.yaml").write_text(
            "id: default\nextends: kraft-default:default\n"
            "overrides:\n  spec.main.author: {policy: {time_cap_minutes: 5}}\n"
        )
    if mode == "broken-policy":
        (config / "policy.yaml").write_text("{{{ not yaml")
    wheel = {
        "unchanged": _release_wheel,
        "broken-policy": _release_wheel,
        "renamed-node": lambda p: _release_wheel(p, "ship"),
        "breaks-override": lambda p: _release_wheel(p, rename_task=True),
    }.get(mode)

    def download(url, dest, **_):
        if mode == "offline":
            raise httpx.ConnectError("no network")
        if wheel is None:
            return _wheel(dest, {"kraft/__init__.py": b""})
        return Path(shutil.copy(wheel(tmp_path), dest))

    monkeypatch.setattr(update, "download", download)
    monkeypatch.setattr(update, "release_for", lambda tag, **_: None)
    got = builtin.preview(
        update.Release("v9.0.0", "" if mode == "no-url" else "u"), config, tmp_path / "run"
    )
    text = builtin.show(got)
    if says is None:
        assert text == "kraft-default: unchanged"
        assert not builtin.needs_answer(got)
    else:
        assert says in text
        assert builtin.needs_answer(got)
        assert bool(got.breaks) == (mode == "breaks-override"), got.breaks
        if got.breaks:
            assert "chains/default.yaml" in got.breaks[0]


def test_a_recorded_hold_is_read_back(tmp_path: Path) -> None:
    assert builtin.recorded(tmp_path) is None
    builtin.record(tmp_path, status="held", from_="2.5.0", to="2.6.0", lines=["x"])
    assert builtin.recorded(tmp_path)["status"] == "held"
