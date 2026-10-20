"""`addons.update` for an add-on that builds on another: a child is judged
together with its ancestors, and a parent's update with its children
(design "Judging a child", "Across collections", "Versions")."""

import pytest
import yaml
from support.addons import (
    AGENT,
    addon_json,
    chain,
    edit_addons_yaml,
    instance,
    make_collection,
    publish,
)

from kraft.addons import manifest, update
from kraft.addons.config import AddonEntry

PARENT = "company@corp"
CHILD = "team@payments"
COMPANY = {
    "version": "3.2.0",
    "library": {
        "steering": {"house": {"instructions": "company rules"}},
        "tasks": {"verify": AGENT, "hotfix": AGENT},
    },
    "chains": {"default": chain("verify"), "hotfix": chain("hotfix")},
    "skills": {"notes": "the company method"},
    "profiles": {"slow": {"model": {"codex": "gpt-5"}}},
}
#: Reaches its parent by its name in every key that takes a qualified name.
SUPERED = {
    "extends": "company:verify",
    "skill": "company:notes",
    "steering": ["company:house"],
    "profile": "company:slow",
}
TEAM = {
    "library": {"tasks": {"check": SUPERED}},
    "chains": {"ship": chain("check"), "default": {"extends": "company:default"}},
}


def _builds_on(spec, parent=PARENT, accepts="3.1"):
    requires = {"kraft": "2", "harnesses": ["codex"], "addons": {parent: accepts}}
    return {**spec, "manifest": {"requires": requires}}


def _run(home, addon_id, *, install, entry=True):
    config_dir, addons_dir = home
    (result,) = update.update(
        [addon_id],
        accept=lambda review: True,
        entries={addon_id: entry} if install else None,
        config_dir=config_dir,
        addons_root=addons_dir,
    )
    return result


def _home(tmp_path, *, company=COMPANY, team=TEAM, collections=("corp", "payments"), corp=None):
    """Collections `corp` (company, and the add-ons `corp` names) and
    `payments` (team); a home that has added `collections` and installed
    nothing."""
    corp = make_collection(tmp_path, {"company": company, **(corp or {})}, name="corp")
    payments = make_collection(tmp_path, {"team": team}, name="payments")
    repos = {"corp": corp, "payments": payments}
    added = {name: {"git": repos[name].as_uri()} for name in collections}
    return repos, instance(tmp_path, added)


def _parent_then_child(tmp_path, **spec):
    repos, home = _home(tmp_path, team=_builds_on(spec.pop("team", TEAM)), **spec)
    assert _run(home, PARENT, install=True).outcome == "applied"
    return repos, home


def _child_on_parent(tmp_path):
    _, home = _parent_then_child(tmp_path)
    return home, CHILD, True


def _child_alone(tmp_path):
    _, home = _home(tmp_path, team=_builds_on(TEAM))
    return home, CHILD, True


def _child_without_the_collection(tmp_path):
    _, home = _home(tmp_path, team=_builds_on(TEAM), collections=("payments",))
    return home, CHILD, True


def _child_out_of_range(tmp_path):
    _, home = _parent_then_child(tmp_path, company={**COMPANY, "version": "4.0.0"})
    return home, CHILD, True


def _both_installed(tmp_path, **spec):
    repos, home = _parent_then_child(tmp_path, **spec)
    assert _run(home, CHILD, install=True).outcome == "applied"
    return repos, home


def _disable_parent(home):
    edit_addons_yaml(home[0], lambda written: written["addons"].update({PARENT: False}))


def _current_child_of_a_disabled_parent(tmp_path):
    _, home = _both_installed(tmp_path)
    _disable_parent(home)
    return home, CHILD, False


def _child_of_a_disabled_parent(tmp_path):
    _, home = _parent_then_child(tmp_path)
    _disable_parent(home)
    return home, CHILD, True


def _parent_moves_out_of_range(tmp_path):
    repos, home = _both_installed(tmp_path)
    publish(repos["corp"], "company", version="4.0.0")
    return home, PARENT, False


def _parent_drops_what_the_child_extends(tmp_path):
    repos, home = _both_installed(tmp_path)
    renamed = {**COMPANY["library"], "tasks": {"checked": AGENT, "hotfix": AGENT}}
    publish(
        repos["corp"],
        "company",
        version="3.3.0",
        library=renamed,
        chains={"default": chain("checked")},
    )
    return home, PARENT, False


def _parent_takes_a_parent_named_like_its_child(tmp_path):
    """Company now builds on `team@corp`: below it, `team@payments` would have
    an ancestor with its own name, and drop."""
    repos, home = _both_installed(tmp_path, corp={"team": {"library": {"tasks": {"x": AGENT}}}})
    corp_team = AddonEntry(**{"as": "corp-team"})
    assert _run(home, "team@corp", install=True, entry=corp_team).outcome == "applied"
    on_team = _builds_on({}, parent="team@corp", accepts="1")
    publish(repos["corp"], "company", version="3.3.0", **on_team)
    return home, PARENT, False


def _grandparent_qualifier(tmp_path):
    dept = _builds_on({"library": {"tasks": {"scan": AGENT}}})
    team = {"library": {"tasks": {"check": {**AGENT, "extends": "company:verify"}}}}
    team = _builds_on(team, parent="dept@corp", accepts="1")
    repos, home = _home(tmp_path, corp={"dept": dept}, team=team)
    for addon_id in (PARENT, "dept@corp"):
        assert _run(home, addon_id, install=True).outcome == "applied"
    return home, CHILD, True


def _removes(**names):
    def setup(tmp_path):
        team = {**TEAM, "library": {**TEAM["library"], "remove": names}}
        _, home = _parent_then_child(tmp_path, team=team)
        return home, CHILD, True

    return setup


def _written(home):
    files = (home[0] / "addons.yaml", home[0] / "addons.lock")
    return [f.read_text() if f.exists() else None for f in files]


@pytest.mark.parametrize(
    ("setup", "outcome", "message"),
    [
        pytest.param(_child_on_parent, "applied", None, id="child-installs-on-installed-parent"),
        pytest.param(
            _child_alone,
            "refused",
            "its parent company@corp is not installed; install it first: "
            "kraft admin addon install company@corp",
            id="child-missing-parent",
        ),
        pytest.param(
            _child_without_the_collection,
            "refused",
            "comes from collection corp, which this instance has not added; add it first: "
            "kraft admin addon collection add",
            id="child-missing-parent-collection",
        ),
        pytest.param(
            _child_out_of_range,
            "refused",
            (
                "its parent company@corp is 4.0.0, outside its range 3.1 (needs 3.x)",
                "point collection corp's ref at a release it accepts, or wait for "
                "team@payments to accept 4.0.0",
            ),
            id="child-out-of-range",
        ),
        pytest.param(
            _current_child_of_a_disabled_parent,
            "current",
            "its parent company@corp is not loaded: is disabled",
            id="current-child-of-a-disabled-parent",
        ),
        pytest.param(
            _child_of_a_disabled_parent,
            "refused",
            "is not loaded: is disabled; enable it first: kraft admin addon enable company@corp",
            id="child-of-a-disabled-parent",
        ),
        pytest.param(
            _parent_moves_out_of_range,
            "refused",
            (
                "would leave team@payments outside its range 3.1",
                "Disable team@payments first, then update it to a release that accepts 4.0.0",
            ),
            id="parent-update-leaves-child-out-of-range",
        ),
        pytest.param(
            _parent_drops_what_the_child_extends,
            "refused",
            "chain team:ship would stop resolving",
            id="parent-update-breaks-child-chain",
        ),
        pytest.param(
            _parent_takes_a_parent_named_like_its_child,
            "refused",
            "team@payments would no longer load: its ancestor team@corp has its name team",
            id="parent-update-drops-a-descendant",
        ),
        pytest.param(
            _grandparent_qualifier,
            "refused",
            "extends 'company:verify' reaches outside the add-on; an add-on reaches no add-on "
            "but its parent (dept@corp)",
            id="qualifier-grandparent",
        ),
        pytest.param(
            _removes(chains=["hotfx"]),
            "refused",
            "library.yaml: remove.chains: hotfx is not inherited from company",
            id="remove-typo",
        ),
        pytest.param(
            _removes(tasks=["verify"]),
            "refused",
            # Naming both: the chain that still runs it, and what it runs.
            ("its chain team:default does not resolve: ", "no task named 'team:verify'"),
            id="remove-breaks-a-reference",
        ),
    ],
)
def test_a_child_is_judged_with_its_ancestors(tmp_path, setup, outcome, message):
    home, addon_id, install = setup(tmp_path)
    before = _written(home)

    result = _run(home, addon_id, install=install)

    assert result.outcome == outcome, result.problems
    parts = (message,) if isinstance(message, str) else message
    told = [*result.problems, *([result.note] if result.note else [])]
    said = [why for why in told if all(part in why for part in parts or ())]
    assert parts is None or said, result.problems
    if outcome == "refused":
        # Each reason once: a child left behind is not also said to drop.
        assert len(result.problems) == 1, result.problems
        assert _written(home) == before


def _manifest(parent=PARENT):
    requires = {"kraft": "2", "harnesses": ["codex"], "addons": {parent: "3"}} if parent else None
    data = addon_json("team", **({"requires": requires} if requires else {}))
    return manifest.addon(data, "addon.json")


def _library(**task):
    library = {"tasks": {"t": {**AGENT, **task}}}
    return {"library.yaml": ("100644", yaml.safe_dump(library).encode())}


@pytest.mark.parametrize(
    ("parent", "task", "why"),
    [
        pytest.param(PARENT, {"extends": "company:verify"}, None, id="parent-extends"),
        pytest.param(PARENT, {"steering": ["company:house"]}, None, id="parent-steering"),
        pytest.param(PARENT, {"profile": "company:slow"}, None, id="parent-profile"),
        pytest.param(PARENT, {"skill": "company:notes"}, None, id="parent-skill"),
        pytest.param(
            PARENT,
            {"extends": "other:verify"},
            "an add-on reaches no add-on but its parent (company@corp)",
            id="other-extends",
        ),
        pytest.param(
            PARENT,
            {"skill": "other:notes"},
            "an add-on reaches no add-on but its parent (company@corp)",
            id="other-skill",
        ),
        pytest.param(
            None,
            {"extends": "company:verify"},
            "reaches outside the add-on; an add-on is self-contained",
            id="no-parent",
        ),
    ],
)
def test_an_addon_reaches_only_its_parent(parent, task, why):
    """The parent's name is accepted wherever a qualified name is; any other
    add-on's namespace, installed or not, is refused."""
    found = update.problems(
        _manifest(parent), _library(**task), other_namespaces={"company", "other"}
    )
    if why is None:
        assert found == []
    else:
        assert len(found) == 1 and why in found[0], found
