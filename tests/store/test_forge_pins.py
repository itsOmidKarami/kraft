"""`forge_pins`: the account and backend an item started with, by mount."""

from __future__ import annotations

from adapters.forge.nodes import back_half

from kraft import store


async def test_pins_accumulate_by_mount_and_replace_per_mount(database, item_on):
    await item_on(back_half())
    assert database.read(lambda c: store.forge_pins(c, "w1")) == {}
    await database.write(lambda c: store.set_forge_pin(c, "w1", "", "a", "gh"))
    await database.write(lambda c: store.set_forge_pin(c, "w1", "libs/core", None, "glab"))
    await database.write(lambda c: store.set_forge_pin(c, "w1", "", "b", "gh"))
    assert database.read(lambda c: store.forge_pins(c, "w1")) == {
        "": {"account": "b", "backend": "gh"},
        "libs/core": {"account": None, "backend": "glab"},
    }
