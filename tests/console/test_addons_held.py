"""Add-ons `u` on a release a child holds back: a move held by a range is
waiting, like a declined one, and one held by its own chain break is an error,
as the CLI's exit 2 says. The fixtures are `test_addons`'s."""

# ruff: noqa: F811 -- `acme` and the harness fixtures are imported, taken by name.
import pytest
from support.addons import AGENT, chain, child_manifest, publish

from console.test_addons import ID, _asking, _shown, _toasts, acme  # noqa: F401
from kraft.addons import update as addon_update
from kraft.addons.config import AddonsLock
from kraft.config import write_yaml

CHILD = "tools@acme"


def _tools_builds_on_release(acme):
    """`tools` 1.0.0, a child of `release` at range `1` with a task `scan`,
    installed beside `release`."""
    acme.install()
    publish(
        acme.repo,
        "tools",
        manifest=child_manifest(ID, "1"),
        library={"tasks": {"base": AGENT, "scan": AGENT}},
    )
    addon_update.update(
        [CHILD],
        accept=lambda _: True,
        entries={CHILD: True},
        config_dir=acme.config,
        addons_root=acme.config.parent / "run" / "addons",
    )


def _held_by_a_range(acme):
    publish(acme.repo, "release", version="2.0.0")


def _held_by_a_break(acme):
    """release 1.1.0 moves; tools 1.1.0 drops `scan`, which a chain extends."""
    write_yaml(acme.config / "chains" / "mine.yaml", chain("tools:scan"))
    publish(acme.repo, "release", version="1.1.0")
    publish(acme.repo, "tools", version="1.1.0", library={"tasks": {"base": AGENT}})


@pytest.mark.parametrize(
    ("held", "answer", "said", "error", "version"),
    [
        pytest.param(_held_by_a_range, None, "release@acme: held", False, "1.0.0", id="range"),
        pytest.param(
            _held_by_a_break,
            ["shift+tab", "enter"],
            "release@acme: applied\ntools@acme",
            True,
            "1.1.0",
            id="break",
        ),
    ],
)
def test_u_on_a_held_update_is_an_error_only_for_a_break(
    board, acme, held, answer, said, error, version
):
    _tools_builds_on_release(acme)
    held(acme)
    console = board([])

    async def scenario(app, pilot):
        await _shown(console, app, pilot)
        await pilot.press("j", "u")
        if answer:
            await console.until(pilot, lambda: _asking(app))
            await pilot.press(*answer)
        await console.until(pilot, lambda: _toasts(app))
        text, is_error = _toasts(app)[0]
        return ": ".join(text.split(": ")[:2]), is_error

    assert console.run(scenario, notifications=True) == (said, error)
    assert AddonsLock.load(acme.config / "addons.lock").addons[ID].version == version
