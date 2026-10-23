"""Where a step runs: precedence, and what a machine or a checkout cannot take."""

import json

import pytest
from support.chain_builders import v1_node
from support.harness import entry_of, make_repo, v1_chain

from kraft.remote import placement, registry


def _workers():
    return registry.WorkersFile.model_validate(
        {
            "workers": {
                "gpu": {"ssh": "gpu", "repos": ["kraft"]},
                "any-box": {"ssh": "any-box"},
            }
        }
    )


@pytest.mark.parametrize(
    ("node_ov", "item_ov", "library", "want"),
    [
        (None, None, None, "local"),
        (None, None, "gpu", "gpu"),
        (None, "box", "gpu", "box"),
        ("any", "box", "gpu", "any"),
        ("local", None, "gpu", "local"),
    ],
    ids=[
        "default-local",
        "library",
        "item-beats-library",
        "node-beats-item",
        "node-can-force-local",
    ],
)
def test_the_most_specific_setting_wins(node_ov, item_ov, library, want):
    row = {
        "agent_overrides": json.dumps({"runner": item_ov}) if item_ov else None,
        "node_overrides": json.dumps({"build": {"runner": node_ov}}) if node_ov else None,
    }
    assert placement.runner_for(row, library, "build") == want


@pytest.mark.parametrize(
    ("runner", "repos", "says"),
    [
        ("local", ["kraft"], None),
        ("nope", ["kraft"], "'nope' is not a machine in workers.yaml"),
        ("gpu", ["kraft"], None),
        ("gpu", ["infra"], "machine 'gpu' is not allowed for repo 'infra'"),
        ("any", ["infra"], None),  # any-box has no repos: limit
        ("any", [], None),
    ],
    ids=[
        "local",
        "unknown-machine",
        "allowed",
        "repo-not-allowed",
        "any-finds-one",
        "any-with-no-repos",
    ],
)
def test_a_machine_that_is_unknown_or_not_allowed_for_the_repo_is_refused(runner, repos, says):
    entries = [entry_of({"path": f"/r/{n}", "name": n}) for n in repos]
    got = placement.refusal(runner, workers=_workers(), repos=entries)
    assert (got is None) if says is None else (says in got)


def test_any_with_no_machine_allowed_is_refused():
    only_gpu = registry.WorkersFile.model_validate(
        {"workers": {"gpu": {"ssh": "gpu", "repos": ["kraft"]}}}
    )
    repos = [entry_of({"path": "/r/infra", "name": "infra"})]
    assert "no machine" in placement.refusal("any", workers=only_gpu, repos=repos)


@pytest.mark.parametrize(
    ("kind", "says"),
    [("submodule", "submodules"), ("lfs", "Git LFS"), ("plain", None)],
)
def test_a_checkout_a_single_bundle_cannot_carry_is_named(tmp_path, kind, says):
    repo = make_repo(tmp_path)
    if kind == "submodule":
        (repo / ".gitmodules").write_text('[submodule "x"]\n\tpath = x\n\turl = ./x\n')
    if kind == "lfs":
        (repo / ".gitattributes").write_text("*.bin filter=lfs diff=lfs merge=lfs -text\n")
    got = placement.checkout_problem(repo)
    assert (got is None) if says is None else (says in got)


def test_workspace_members_are_named(tmp_path):
    assert "workspace members" in placement.checkout_problem(
        make_repo(tmp_path), members=["libs/a"]
    )


def test_filing_problems_name_both_settings_for_in_place_and_sandbox(tmp_path):
    repo = make_repo(tmp_path)
    got = placement.filing_problems(
        ["gpu"],
        workers=_workers(),
        repos=[entry_of({"path": str(repo), "name": "kraft"})],
        in_place=True,
        sandboxed=True,
        repo_path=repo,
    )
    assert any("in-place" in p and "gpu" in p for p in got)
    assert any("sandbox" in p and "gpu" in p for p in got)
    assert (
        placement.filing_problems(
            ["local"], workers=_workers(), repos=[], in_place=True, sandboxed=True, repo_path=repo
        )
        == []
    )


def test_library_runners_lists_every_machine_the_chain_names():
    chain = v1_chain(
        [v1_node("a", runner="gpu"), v1_node("b", runner="local"), v1_node("c")], repo="/r"
    )
    assert placement.library_runners(chain) == {"gpu"}


def test_a_nodes_own_launches_follow_its_placement_and_then_the_items_overrides():
    chain = v1_chain([v1_node("a", runner="gpu"), v1_node("b")], repo="/r")
    row = {"materialized_chain": chain.to_json(), "agent_overrides": None, "node_overrides": None}
    assert (placement.node_runner(row, "a"), placement.node_runner(row, "b")) == ("gpu", "local")
    row["agent_overrides"] = '{"runner": "box"}'
    assert (placement.node_runner(row, "a"), placement.node_runner(row, "b")) == ("box", "box")
    assert placement.remote("local") is None and placement.remote("box") == "box"
    assert placement.remote(None) is None
