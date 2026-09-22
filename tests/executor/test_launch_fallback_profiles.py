"""Launch fallback over agent profiles (Kraft-0a3h8 on Kraft-ps1ao): an entry
selecting a `profile:`, and a profile's own `fallback:` list, read live from
`harnesses.yaml` like the profile body. Launches are
`tests/support/fake_agent.py` on the `fake` provider (see
tests/executor/test_launch_fallback.py)."""

import os
import time
from pathlib import Path

from support.harness import v1_chain, v1_walk, write_agent_profiles, write_harness_profiles

#: The tiers on the `fake` provider every harness profile here runs.
PROFILES = {
    "deep": {"effort": "high", "model": {"fake": "opus"}, "fallback": [{"profile": "strong"}]},
    "strong": {"effort": "high", "model": {"fake": "sonnet"}, "fallback": [{"model": "haiku"}]},
    "codexish": {"model": {"codex": "gpt-5.6-terra"}},
}


def _setup(fake_agent, monkeypatch, *, limit=("opus",), profiles=PROFILES, harnesses=None):
    for where in (fake_agent.templates, Path(os.environ["KRAFT_HOME"]) / "templates"):
        if harnesses:
            write_harness_profiles(where, harnesses)
        write_agent_profiles(where, profiles)
    monkeypatch.setenv("KRAFT_FAKE_AGENT_RATE_LIMIT_MODELS", ",".join(limit))
    monkeypatch.setenv("KRAFT_FAKE_AGENT_RESETS_AT", str(int(time.time()) + 3600))


def _walk(tmp_path, repo, **task):
    task = {"id": "implement", "kind": "agent", "harness": "claude", "prompt": "Do it.", **task}
    chain = [{"id": "build", "kind": "exec", "tasks": [task]}]
    return v1_walk(tmp_path, v1_chain(chain, repo=repo), repo=repo)


def _launched(fake_agent) -> list[tuple]:
    out = []
    for argv in fake_agent.argv():
        pick = [argv[argv.index(f) + 1] if f in argv else None for f in ("--model", "--effort")]
        out.append(tuple(pick))
    return out


def _fallbacks(evts):
    return [e["payload"] for e in evts if e["type"] == "launch_fallback"]


async def test_a_profiles_own_list_moves_a_limited_launch_to_another_tier(
    tmp_path, repo, fake_agent, monkeypatch
):
    """deep's list names `strong`; strong's own list (`haiku`) is not followed."""
    _setup(fake_agent, monkeypatch, limit=("opus", "sonnet"))

    status, evts, sessions, _ = await _walk(tmp_path, repo, profile="deep")

    assert status == "rate_limited"
    assert _launched(fake_agent) == [("opus", "high"), ("sonnet", "high")]
    assert [s["status"] for s in sessions] == ["rate_limited", "rate_limited"]
    first, last = _fallbacks(evts)
    assert first["from"] == {
        "harness": "claude",
        "profile": "deep",
        "model": "opus",
        "effort": "high",
    }
    assert first["to"] == {
        "harness": "claude",
        "profile": "strong",
        "model": "sonnet",
        "effort": "high",
    }
    assert (last["reason"], last["to"]) == ("rate_limit_hit", None)


async def test_a_model_entry_replaces_a_profile_route_whole(
    tmp_path, repo, fake_agent, monkeypatch
):
    """`{ model: sonnet }` on a `profile: deep` task: sonnet, and deep's effort is not kept."""
    _setup(fake_agent, monkeypatch)

    status, *_ = await _walk(tmp_path, repo, profile="deep", fallback=[{"model": "sonnet"}])

    assert status == "completed"
    assert _launched(fake_agent) == [("opus", "high"), ("sonnet", None)]


async def test_a_tasks_empty_list_overrides_its_profiles(tmp_path, repo, fake_agent, monkeypatch):
    _setup(fake_agent, monkeypatch)

    status, evts, _, row = await _walk(tmp_path, repo, profile="deep", fallback=[])

    assert status == "rate_limited"
    assert _launched(fake_agent) == [("opus", "high")]
    assert _fallbacks(evts) == []
    assert row["retry_at"]


async def test_an_entry_profile_with_no_model_for_the_provider_is_skipped(
    tmp_path, repo, fake_agent, monkeypatch
):
    """`codexish` names no model for `fake`: unavailable at launch, so skipped."""
    _setup(fake_agent, monkeypatch)

    status, evts, _, _ = await _walk(
        tmp_path,
        repo,
        profile="deep",
        fallback=[{"profile": "codexish"}, {"profile": "strong"}],
    )

    assert status == "completed"
    assert _launched(fake_agent) == [("opus", "high"), ("sonnet", "high")]
    skipped = _fallbacks(evts)[1]
    assert skipped["reason"] == "unavailable"
    assert "profile 'codexish' has no model for provider 'fake'" in skipped["detail"]


async def test_a_profile_list_entry_outside_allowed_harnesses_never_runs(
    tmp_path, repo, fake_agent, monkeypatch
):
    """`fallback-never-escapes-allowed-harnesses`: a profile's list is live, so
    materialization cannot see it; the launch refuses its harness instead."""
    _setup(
        fake_agent,
        monkeypatch,
        profiles={**PROFILES, "deep": {**PROFILES["deep"], "fallback": [{"harness": "codex"}]}},
    )

    status, evts, _, _ = await _walk(
        tmp_path, repo, profile="deep", policy={"allowed_harnesses": ["claude"]}
    )

    assert status == "rate_limited"
    assert _launched(fake_agent) == [("opus", "high")]
    refused = _fallbacks(evts)[1]
    assert refused["from"]["harness"] == "codex"
    assert refused["reason"] == "unavailable"
    assert "allowed_harnesses" in refused["detail"]
