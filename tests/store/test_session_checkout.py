"""`store.create_session(takes_checkout=True)`: the checkout lease for an
agent session that runs outside the walk."""

import pytest
from support.harness import v1_named_chain

from kraft import store
from kraft.templates.environment import WorkItemTarget


@pytest.mark.parametrize(
    "takes_checkout", [True, False], ids=["outside-the-walk", "any-other-session"]
)
async def test_an_agent_outside_the_walk_gets_no_session_beside_a_working_in_place_item(
    item_on, run_dirs, takes_checkout
):
    """Refused in the write that would record it, so no window lets one start."""
    in_place = WorkItemTarget.for_repository("sample", in_place=True)
    chain = v1_named_chain(run_dirs.base / "t")
    await item_on(chain, wid="a", target=in_place, title="A")
    b = await item_on(chain, "implementation", wid="b", target=in_place, status="needs_human")

    def create(c):
        return store.create_session(
            c,
            id="s1",
            work_item_id="b",
            node_id="implementation",
            hook_point="escalation",
            log_path="l",
            result_path="r",
            takes_checkout=takes_checkout,
        )

    if takes_checkout:
        with pytest.raises(store.CheckoutBusy, match="in use by A") as busy:
            await b.database.write(create)
        assert busy.value.holder == "a"
    else:
        await b.database.write(create)
    assert len(b.sessions()) == (0 if takes_checkout else 1)
