"""Task 5: every agent dispatch carries the item's unanswered review threads
(review threads anywhere §1). Split out of test_dispatch.py to keep it under
the line budget."""

from __future__ import annotations

from support.harness import v1_chain, v1_walk

from kraft import store


def _walk(tmp_path, repo, chain, **kwargs):
    """File `chain` (node list or `ResolvedChain`) on `repo` and walk it once."""
    return v1_walk(tmp_path, v1_chain(chain, repo=repo), repo=repo, **kwargs)


async def test_the_implementer_prompt_carries_a_mid_run_thread(tmp_path, repo, fake_agent):
    """review threads anywhere §1: every agent dispatch carries the item's
    unanswered review threads, not only a rejection's own note. Seeds a
    published thread against the item's id right after it is filed, before
    the walk's implementer dispatch reads it."""
    seeded = {}

    async def seed(database):
        seeded["tid"] = await database.write(
            lambda c: store.create_thread(
                c, wid="w1", gate=None, anchor_sha="h", body="evict LRU", label="must_fix"
            )
        )
        await database.write(
            lambda c: store.submit_review(
                c,
                wid="w1",
                gate=None,
                outcome="comment",
                summary=None,
                head_sha="h",
                base_sha="h",
            )
        )

    status, *_ = await _walk(tmp_path, repo, fake_agent.quick_task, after_item=seed)

    assert status == "completed"
    [prompt] = fake_agent.prompts()
    assert f"[{seeded['tid']}]" in prompt
