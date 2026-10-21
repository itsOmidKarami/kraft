"""Auto-update: what an add-on set to update itself may take with nobody
watching, what is held for a person, and where the outcome is recorded."""

import shutil

import pytest
from support.addons import (
    AGENT,
    chain,
    child_manifest,
    edit_addons_yaml,
    instance,
    make_collection,
    make_tree,
    publish,
)
from support.harness import git

from kraft.addons import fetch, load, review, update
from kraft.addons.config import AddonEntry, AddonsLock

ID = "release@acme"
GATED = {"nodes": [*chain("base")["nodes"], {"id": "ok", "kind": "gate"}]}
RELEASE = {
    "library": {"tasks": {"base": {**AGENT, "policy": {"budget_usd": 5}}}},
    "chains": {"ship": GATED},
}


@pytest.fixture
def home(tmp_path):
    """`release@acme` installed and set to update itself, `tools@acme`
    installed and not; the collection, the config directory and `run/addons`."""
    repo = make_collection(tmp_path, {"release": RELEASE, "tools": RELEASE})
    config_dir, addons_dir = instance(tmp_path, {"acme": {"git": repo.as_uri()}})
    results = update.update(
        [ID, "tools@acme"],
        accept=lambda review: True,
        entries={ID: AddonEntry(auto_update=True), "tools@acme": True},
        config_dir=config_dir,
        addons_root=addons_dir,
    )
    assert [r.outcome for r in results] == ["applied", "applied"]
    return repo, config_dir, addons_dir


def _versions(config_dir):
    return {i: e.version for i, e in AddonsLock.load(config_dir / "addons.lock").addons.items()}


def _collection_on(written):
    written["collections"]["acme"]["auto_update"] = True
    written["addons"] = {ID: True, "tools@acme": True}


def _neither(written):
    written["addons"][ID] = True


@pytest.mark.parametrize(
    "setting, updated",
    [(None, [ID]), (_collection_on, [ID, "tools@acme"]), (_neither, [])],
    ids=["addon-on", "collection-on", "neither-is-not-fetched"],
)
def test_who_auto_updates(home, monkeypatch, setting, updated):
    repo, config_dir, addons_dir = home
    if setting:
        edit_addons_yaml(config_dir, setting)
    for name in ("release", "tools"):
        publish(repo, name, version="1.1.0", skills={"notes": "new in 1.1.0"})
    if not updated:
        monkeypatch.setattr(fetch, "fetch", lambda *a: pytest.fail("fetched a collection"))

    outcomes = update.auto_update(config_dir, addons_dir)

    assert {i: o["outcome"] for i, o in outcomes.items()} == dict.fromkeys(updated, "applied")
    assert [i for i, v in _versions(config_dir).items() if v == "1.1.0"] == updated
    assert sorted(update.read_status(addons_dir)) == sorted(updated)


OTHER = {"kraft": "2", "harnesses": ["codex"], "profiles": []}


@pytest.mark.parametrize(
    "new, why",
    [
        ({"version": "1.1.0", "chains": {"ship": chain("base")}}, "gate removed"),
        (
            {
                "version": "1.1.0",
                "library": {"tasks": {"base": {**AGENT, "policy": {"budget_usd": 9}}}},
            },
            "limit budget_usd raised, 5 -> 9",
        ),
        ({"version": "1.1.0", "manifest": {"requires": OTHER}}, "requires changed"),
        ({"version": "0.9.0", "skills": {"notes": "older"}}, "downgrade: 1.0.0 -> 0.9.0"),
        ({"version": "1.1.0-rc.1", "skills": {"notes": "a candidate"}}, "moves onto a pre-release"),
    ],
    ids=["gate-removed", "limit-raised", "requires", "downgrade", "pre-release"],
)
def test_auto_update_holds(home, new, why):
    """Anything that can change how far a run reaches waits for a person."""
    repo, config_dir, addons_dir = home
    publish(repo, "release", **new)
    before = (config_dir / "addons.lock").read_text()

    outcome = update.auto_update(config_dir, addons_dir)[ID]

    assert outcome["outcome"] == "held" and why in outcome["message"], outcome
    assert outcome["message"].endswith("; run kraft admin addon update")
    assert (config_dir / "addons.lock").read_text() == before
    assert update.read_status(addons_dir)[ID]["outcome"] == "held"


def _member(addon_id, reach):
    return review.Review(addon_id, "1.0.0", "1.1.0", reach=("gate removed",) if reach else ())


@pytest.mark.parametrize(
    "reaches, expected",
    [
        ({"release@acme": False}, {}),
        ({"release@acme": True}, {"release@acme": "gate removed"}),
        (
            {"company@acme": True, "team@acme": False},
            {"company@acme": "gate removed", "team@acme": "with company@acme: gate removed"},
        ),
        (
            {"company@acme": True, "team@acme": True},
            {"company@acme": "gate removed", "team@acme": "gate removed"},
        ),
    ],
    ids=["one-clean", "one-held", "relative-holds", "each-own-reason"],
)
def test_a_group_is_held_whole(reaches, expected):
    """A relative that widens nothing is held with the one that does; a group
    of one is held for its own reason, as before."""
    group = review.Group(tuple(_member(i, reach) for i, reach in reaches.items()))

    assert update.unattended_holds(group) == expected


def _listed_not_locked(repo, config_dir):
    publish(repo, "extra", **RELEASE)
    edit_addons_yaml(
        config_dir, lambda w: w["addons"].update({"extra@acme": {"auto_update": True}})
    )


def _same_version_new_commit(repo, config_dir):
    publish(repo, "release", skills={"notes": "changed without a version"})


def _ref_changed_by_hand(repo, config_dir):
    git(repo, "tag", "v2")
    publish(repo, "release", version="1.1.0", skills={"notes": "new"})
    edit_addons_yaml(config_dir, lambda w: w["collections"]["acme"].update(ref="main"))


def _url_changed_by_hand(repo, config_dir):
    fork = repo.parent / "fork"
    shutil.copytree(repo, fork)
    publish(fork, "release", version="1.1.0", skills={"notes": "from somewhere else"})
    edit_addons_yaml(config_dir, lambda w: w["collections"]["acme"].update(git=fork.as_uri()))


def _orphaned_in_the_lock(repo, config_dir):
    edit_addons_yaml(config_dir, lambda w: w["addons"].pop("tools@acme"))


@pytest.mark.parametrize(
    "happens, outcome",
    [
        (_listed_not_locked, None),
        (_orphaned_in_the_lock, None),
        (_same_version_new_commit, "up to date"),
        (_ref_changed_by_hand, "held: ref"),
        (_url_changed_by_hand, "held: collection URL"),
    ],
    ids=["installs", "an-orphan", "re-installs", "applies-a-ref-change", "follows-a-new-url"],
)
def test_auto_update_never(home, happens, outcome):
    repo, config_dir, addons_dir = home
    happens(repo, config_dir)
    before = (config_dir / "addons.lock").read_text()

    outcomes = update.auto_update(config_dir, addons_dir)

    assert sorted(outcomes) == [ID]
    state, _, what = (outcome or "up to date").partition(": ")
    assert outcomes[ID]["outcome"] == state
    assert (config_dir / "addons.lock").read_text() == before
    if state == "held":
        assert outcomes[ID]["message"] == f"{what} change pending; run kraft admin addon update"


def test_a_failed_auto_update_keeps_the_lock_and_records_why(home):
    repo, config_dir, addons_dir = home
    repo.rename(repo.parent / "gone")
    before = _versions(config_dir)

    outcome = update.auto_update(config_dir, addons_dir)[ID]

    assert (outcome["outcome"], outcome["kind"]) == ("failed", "network")
    assert _versions(config_dir) == before
    recorded = update.read_status(addons_dir)[ID]
    assert (recorded["outcome"], recorded["kind"]) == ("failed", "network") and recorded["at"]


def test_a_newer_pre_release_follows_a_pre_release(home):
    """An add-on already on a candidate takes the next one; only the move
    onto a pre-release is held."""
    repo, config_dir, addons_dir = home
    publish(repo, "release", version="1.1.0-rc.1", skills={"notes": "a candidate"})
    update.update([ID], accept=lambda r: True, config_dir=config_dir, addons_root=addons_dir)
    publish(repo, "release", version="1.1.0-rc.2", skills={"notes": "the next candidate"})

    assert update.auto_update(config_dir, addons_dir)[ID]["outcome"] == "applied"
    assert _versions(config_dir)[ID] == "1.1.0-rc.2"


def test_a_person_taking_a_held_update_clears_its_warning(home):
    """Health and doctor stop saying "held" once the update it named is applied."""
    repo, config_dir, addons_dir = home
    publish(repo, "release", version="1.1.0", chains={"ship": chain("base")})
    assert update.auto_update(config_dir, addons_dir)[ID]["outcome"] == "held"
    publish(repo, "tools", version="1.1.0", chains={"ship": chain("base")})

    update.update([ID], accept=lambda r: True, config_dir=config_dir, addons_root=addons_dir)

    assert update.read_status(addons_dir) == {}
    assert _versions(config_dir)[ID] == "1.1.0"


def test_a_range_held_auto_update_waits(tmp_path):
    """A parent's release that an installed child's range rejects waits, with
    the range that holds it, for the child to accept it or a person to act."""
    company, team = RELEASE, {**RELEASE, "parent": ("company@corp", "1")}
    repos, (config_dir, addons_dir) = make_tree(
        tmp_path, {"company@corp": company, "team@corp": team}
    )
    on = {"company@corp": {"auto_update": True}}
    edit_addons_yaml(config_dir, lambda w: w["addons"].update(on))
    publish(repos["corp"], "company", version="2.0.0")
    before = (config_dir / "addons.lock").read_text()

    outcome = update.auto_update(config_dir, addons_dir)["company@corp"]

    assert outcome == {
        "outcome": "held",
        "message": "company@corp 2.0.0 is outside team@corp's range 1 (needs 1.x); run kraft "
        "admin addon update to move it with its relatives, or wait for a release that fits",
    }
    assert (config_dir / "addons.lock").read_text() == before


def test_auto_update_takes_no_relatives(tmp_path, monkeypatch):
    """A relative whose own auto-update is off is not moved by its parent's,
    and its collection is not fetched for it, though its waiting release is
    the one that would let the parent move."""
    company, team = RELEASE, {**RELEASE, "parent": ("company@corp", "1")}
    repos, (config_dir, addons_dir) = make_tree(
        tmp_path, {"company@corp": company, "team@payments": team}
    )
    on = {"company@corp": {"auto_update": True}}
    edit_addons_yaml(config_dir, lambda w: w["addons"].update(on))
    publish(repos["corp"], "company", version="2.0.0")
    publish(
        repos["payments"], "team", version="1.1.0", manifest=child_manifest("company@corp", "2")
    )
    before = (config_dir / "addons.lock").read_text()
    fetched = []
    real = fetch.fetch

    def spy(addons_dir, name, url, ref):
        fetched.append(url)
        return real(addons_dir, name, url, ref)

    monkeypatch.setattr(fetch, "fetch", spy)

    outcomes = update.auto_update(config_dir, addons_dir)

    assert outcomes == {
        "company@corp": {
            "outcome": "held",
            "message": "company@corp 2.0.0 is outside team@payments's range 1 (needs 1.x); run "
            "kraft admin addon update to move it with its relatives, or wait for a release "
            "that fits",
        }
    }
    assert (config_dir / "addons.lock").read_text() == before
    assert fetched == [repos["corp"].as_uri()]


@pytest.mark.parametrize(
    ("team_library", "outcome"),
    [
        ({"tasks": {"pci_review": AGENT}}, "held"),
        ({"tasks": {"mine": AGENT}}, "applied"),
    ],
    ids=["descendant-shadows-a-new-name", "descendant-unaffected"],
)
def test_a_descendants_reach_holds_its_parents_auto_update(tmp_path, team_library, outcome):
    """company's release adds a task, a plain addition for company; a child
    that declares the name now overrides it, and that holds the move for a
    person though the child's own auto-update is off."""
    company, team = RELEASE, {**RELEASE, "library": team_library, "parent": ("company@corp", "1")}
    repos, (config_dir, addons_dir) = make_tree(
        tmp_path, {"company@corp": company, "team@corp": team}
    )
    on = {"company@corp": {"auto_update": True}}
    edit_addons_yaml(config_dir, lambda w: w["addons"].update(on))
    library = {"tasks": {**RELEASE["library"]["tasks"], "pci_review": AGENT}}
    publish(repos["corp"], "company", version="1.1.0", library=library)

    result = update.auto_update(config_dir, addons_dir)["company@corp"]

    assert result["outcome"] == outcome, result
    if outcome == "held":
        assert (
            "team@corp: its task pci_review now overrides company@corp's new task pci_review"
            in result["message"]
        ), result
        assert _versions(config_dir)["company@corp"] == "1.0.0"


def test_a_descendant_that_cannot_be_read_holds_its_parents_auto_update(tmp_path):
    """team's store was changed behind the lock's back, so company's release
    cannot show what it does to team: the move waits for a person."""
    team = {**RELEASE, "library": {"tasks": {"mine": AGENT}}, "parent": ("company@corp", "1")}
    repos, (config_dir, addons_dir) = make_tree(
        tmp_path, {"company@corp": RELEASE, "team@corp": team}
    )
    on = {"company@corp": {"auto_update": True}}
    edit_addons_yaml(config_dir, lambda w: w["addons"].update(on))
    mine = next(a for a in load.installed(config_dir, addons_dir) if a.id == "team@corp")
    ship = mine.root / "chains/ship.yaml"
    ship.parent.chmod(0o755)
    ship.parent.joinpath("also.yaml").hardlink_to(ship)
    library = {"tasks": {**RELEASE["library"]["tasks"], "other": AGENT}}
    publish(repos["corp"], "company", version="1.1.0", library=library)

    result = update.auto_update(config_dir, addons_dir)["company@corp"]

    assert result["outcome"] == "held", result
    assert "team@corp: its store does not read" in result["message"], result
    assert _versions(config_dir)["company@corp"] == "1.0.0"


COMPANY, DEPT, TEAM = "company@corp", "platform-dept@platform", "team-payments@payments"
UNDER = {DEPT: COMPANY, TEAM: DEPT}


@pytest.fixture
def tree_home(tmp_path, monkeypatch):
    """`company@corp` <- `platform-dept@platform` <- `team-payments@payments`,
    each from its own collection at range 1 and set to update itself, beside
    `tools@acme`, a root of its own that is not; then every URL `fetch.fetch`
    is called with, from here on."""
    company, tools = RELEASE, RELEASE
    dept, team = ({**RELEASE, "parent": (UNDER[i], "1")} for i in (DEPT, TEAM))
    repos, (config_dir, addons_dir) = make_tree(
        tmp_path, {COMPANY: company, DEPT: dept, TEAM: team, "tools@acme": tools}
    )
    on = {i: {"auto_update": True} for i in (COMPANY, DEPT, TEAM)}
    edit_addons_yaml(config_dir, lambda w: w["addons"].update(on))
    fetched = []
    real = fetch.fetch

    def spy(addons_dir, name, url, ref):
        fetched.append(url)
        return real(addons_dir, name, url, ref)

    monkeypatch.setattr(fetch, "fetch", spy)
    return repos, config_dir, addons_dir, fetched


def _off(config_dir, *ids):
    edit_addons_yaml(config_dir, lambda w: w["addons"].update(dict.fromkeys(ids, True)))


def _put(repos, addon_id, version, range_=None, *, note=None, **spec):
    """Publish `addon_id` at `version`, under its tree parent at `range_` (1
    when not given) unless it is a root. `note` is content only: a skill
    named for the add-on, so no relative's skill shadows it."""
    name, collection = addon_id.split("@", 1)
    if note is not None:
        spec["skills"] = {f"{name}-notes": note}
    if addon_id in UNDER:
        spec["manifest"] = child_manifest(UNDER[addon_id], range_ or "1")
    publish(repos[collection], name, version=version, **spec)


def _each_publishes(repos, config_dir):
    for addon_id in (COMPANY, DEPT, TEAM):
        _put(repos, addon_id, "1.1.0", note="new in 1.1.0")


def _cross_collection(repos, config_dir):
    _put(repos, COMPANY, "2.0.0")
    _put(repos, DEPT, "1.1.0", "2")


def _team_off(repos, config_dir):
    _off(config_dir, TEAM)
    _each_publishes(repos, config_dir)


def _dept_off_moves(repos, config_dir):
    _off(config_dir, DEPT)
    _put(repos, DEPT, "2.0.0")
    _put(repos, TEAM, "1.1.0", "2")


def _team_off_moves(repos, config_dir):
    _off(config_dir, TEAM)
    _put(repos, TEAM, "1.1.0", "2")
    _put(repos, DEPT, "2.0.0")


def _team_a_directory(repos, config_dir):
    def to_path(written):
        written["collections"]["payments"] = {"path": str(repos["payments"])}
        written["addons"][TEAM] = True

    edit_addons_yaml(config_dir, to_path)
    _put(repos, TEAM, "1.0.0", note="changed on disk")
    for addon_id in (COMPANY, DEPT):
        _put(repos, addon_id, "1.1.0", note="new in 1.1.0")


def _company_drops_a_gate(repos, config_dir):
    _put(repos, COMPANY, "1.1.0", chains={"ship": chain("base")})
    _put(repos, DEPT, "1.1.0", note="content only")


def _company_drops_a_gate_over_dept_off(repos, config_dir):
    _off(config_dir, DEPT)
    _put(repos, COMPANY, "1.1.0", chains={"ship": chain("base")})
    _put(repos, TEAM, "1.1.0", note="content only")


def _company_outgrows_dept(repos, config_dir):
    _put(repos, COMPANY, "2.0.0")


def _team_ref_by_hand(repos, config_dir):
    git(repos["payments"], "tag", "v2")
    _put(repos, TEAM, "1.1.0", note="new")
    edit_addons_yaml(config_dir, lambda w: w["collections"]["payments"].update(ref="main"))
    _put(repos, COMPANY, "1.1.0", note="new")


def _tools_drops_a_gate(repos, config_dir):
    on = {"tools@acme": {"auto_update": True}}
    edit_addons_yaml(config_dir, lambda w: w["addons"].update(on))
    publish(repos["acme"], "tools", version="1.1.0", chains={"ship": chain("base")})
    _each_publishes(repos, config_dir)


def _team_names_a_new_parent(repos, config_dir):
    publish(repos["corp"], "audit", **RELEASE)
    publish(
        repos["payments"],
        "team-payments",
        version="1.1.0",
        manifest=child_manifest("audit@corp", "1"),
    )


ALL = ("corp", "platform", "payments", "acme")
TEAM_OFF = (
    f"{TEAM}'s range 2 does not accept {DEPT} 1.0.0 (needs 2.x); run kraft admin addon update "
    "to move it with its relatives, or wait for a release that fits"
)
OUTGROWN = (
    f"{COMPANY} 2.0.0 is outside {DEPT}'s range 1 (needs 1.x); run kraft admin addon update "
    "to move it with its relatives, or wait for a release that fits"
)
RANGE_RAISED = f"parent range: {COMPANY} 1 -> 2; run kraft admin addon update"
PENDING = f"with {TEAM}: its ref change is pending; run kraft admin addon update"


@pytest.mark.parametrize(
    "setup, outcomes, versions, says, unfetched",
    [
        (
            _each_publishes,
            {COMPANY: "applied", DEPT: "applied", TEAM: "applied"},
            {COMPANY: "1.1.0", DEPT: "1.1.0", TEAM: "1.1.0"},
            {},
            ("acme",),
        ),
        (
            _cross_collection,
            {COMPANY: "held", DEPT: "held", TEAM: "up to date"},
            {COMPANY: "1.0.0", DEPT: "1.0.0", TEAM: "1.0.0"},
            {COMPANY: f"with {DEPT}: {RANGE_RAISED}", DEPT: RANGE_RAISED},
            ("acme",),
        ),
        (
            _team_off,
            {COMPANY: "applied", DEPT: "applied"},
            {COMPANY: "1.1.0", DEPT: "1.1.0", TEAM: "1.0.0"},
            {},
            ("payments", "acme"),
        ),
        (
            _dept_off_moves,
            {COMPANY: "up to date", TEAM: "held"},
            {COMPANY: "1.0.0", DEPT: "1.0.0", TEAM: "1.0.0"},
            {TEAM: TEAM_OFF},
            ("platform", "acme"),
        ),
        (
            _team_off_moves,
            {COMPANY: "up to date", DEPT: "held"},
            {COMPANY: "1.0.0", DEPT: "1.0.0", TEAM: "1.0.0"},
            {DEPT: f"{DEPT} 2.0.0 is outside {TEAM}'s range 1 (needs 1.x); run kraft admin addon"},
            ("payments", "acme"),
        ),
        (
            _team_a_directory,
            {COMPANY: "applied", DEPT: "applied"},
            {COMPANY: "1.1.0", DEPT: "1.1.0", TEAM: "1.0.0"},
            {},
            ("payments", "acme"),
        ),
        (
            _company_drops_a_gate,
            {COMPANY: "held", DEPT: "held", TEAM: "up to date"},
            {COMPANY: "1.0.0", DEPT: "1.0.0", TEAM: "1.0.0"},
            {COMPANY: ("gate removed",), DEPT: (f"with {COMPANY}: ", "gate removed")},
            ("acme",),
        ),
        (
            _company_drops_a_gate_over_dept_off,
            {COMPANY: "held", TEAM: "held"},
            {COMPANY: "1.0.0", DEPT: "1.0.0", TEAM: "1.0.0"},
            {COMPANY: ("gate removed",), TEAM: (f"with {COMPANY}: ", "gate removed")},
            ("platform", "acme"),
        ),
        (
            _company_outgrows_dept,
            {COMPANY: "held", DEPT: "up to date", TEAM: "up to date"},
            {COMPANY: "1.0.0", DEPT: "1.0.0", TEAM: "1.0.0"},
            {COMPANY: OUTGROWN},
            ("acme",),
        ),
        (
            _team_ref_by_hand,
            {COMPANY: "held", DEPT: "held", TEAM: "held"},
            {COMPANY: "1.0.0", DEPT: "1.0.0", TEAM: "1.0.0"},
            {
                TEAM: "ref change pending; run kraft admin addon update",
                COMPANY: PENDING,
                DEPT: PENDING,
            },
            ALL,
        ),
        (
            _tools_drops_a_gate,
            {COMPANY: "applied", DEPT: "applied", TEAM: "applied", "tools@acme": "held"},
            {COMPANY: "1.1.0", DEPT: "1.1.0", TEAM: "1.1.0", "tools@acme": "1.0.0"},
            {"tools@acme": "gate removed"},
            (),
        ),
        (
            _team_names_a_new_parent,
            {COMPANY: "up to date", DEPT: "up to date", TEAM: "failed"},
            {COMPANY: "1.0.0", DEPT: "1.0.0", TEAM: "1.0.0"},
            {TEAM: "its parent audit@corp is not installed; install it first"},
            ("acme",),
        ),
    ],
    ids=[
        "whole-tree-moves",
        "cross-collection-set",
        "one-member-off",
        "child-needs-off-parent",
        "parent-needs-off-child",
        "directory-member-changed",
        "person-hold-holds-tree",
        "held-through-an-off-member",
        "range-hold-waits",
        "hand-change-holds-tree",
        "unrelated-tree-independent",
        "never-pulls-in",
    ],
)
def test_auto_update_moves_a_tree(tree_home, setup, outcomes, versions, says, unfetched):
    """A tree's auto-update-on add-ons move as one set, across collections; an
    add-on whose auto-update is off counts at its locked version, gets no
    entry, and its collection is not fetched for it."""
    repos, config_dir, addons_dir, fetched = tree_home
    setup(repos, config_dir)
    stores = {a.id: a.root for a in load.installed(config_dir, addons_dir)}

    result = update.auto_update(config_dir, addons_dir)

    assert {i: o["outcome"] for i, o in result.items()} == outcomes, result
    for addon_id, fragments in says.items():
        for fragment in fragments if isinstance(fragments, tuple) else (fragments,):
            assert fragment in result[addon_id]["message"], result[addon_id]
    assert _versions(config_dir) == {**versions, "tools@acme": versions.get("tools@acme", "1.0.0")}
    assert sorted(update.read_status(addons_dir)) == sorted(outcomes)
    assert not {repos[c].as_uri() for c in unfetched} & set(fetched), fetched
    if TEAM not in outcomes:
        assert stores[TEAM] == next(
            a.root for a in load.installed(config_dir, addons_dir) if a.id == TEAM
        )
    assert "audit@corp" not in (config_dir / "addons.yaml").read_text()
