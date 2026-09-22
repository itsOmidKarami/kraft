from pathlib import Path

import pytest
from pydantic import ValidationError

from kraft.templates import environment as te


@pytest.fixture
def workspace() -> te.Workspace:
    return te.Workspace(
        id="product",
        root="product_root",
        root_pointer_default="ignore",
        members={"api": te.WorkspaceMember(repository="api", path="services/api")},
    )


# ── areas are not repositories
# (repositories-workspaces-and-areas-are-distinct) ──


def test_area_declares_setup_and_test_scopes():
    area = te.Area(
        paths=["services/api/**"],
        setup="uv sync",
        verification=te.Verification(
            test_scopes=[
                te.TestScope(paths=["services/api/**", "tests/api/**"], command="just test-api")
            ]
        ),
    )
    assert area.setup == "uv sync"
    assert area.verification.test_scopes[0].paths == ["services/api/**", "tests/api/**"]


def test_area_has_no_forge_field_to_declare():
    """An area is never a forge target -- not by a runtime check, but because
    the type has nothing to set (repositories-workspaces-and-areas-are-
    distinct)."""
    assert "forge" not in te.Area.model_fields
    with pytest.raises(ValidationError):
        te.Area.model_validate(
            {"paths": ["x/**"], "forge": {"kind": "github", "project": "acme/x"}}
        )


# ── workspaces (workspace-declares-root-and-members) ──


def test_workspace_declares_root_and_members(workspace):
    assert workspace.root == "product_root"
    assert workspace.members["api"].path == "services/api"
    assert workspace.root_pointer_default == te.RootPointerPolicy.IGNORE


def test_workspace_target_captures_selected_members(workspace):
    target = te.WorkItemTarget.from_selection(workspace, members=["api"], include_root=False)
    assert target.members == ("api",)
    # The mount path is frozen with the selection (`work-item-target-is-typed-
    # and-immutable`): a later edit to the workspace moves nothing under a
    # running item.
    assert target.mounts == {"api": te.WorkspaceMember(repository="api", path="services/api")}
    # Every repository whose policy binds the item, root first (Kraft-jc39p).
    assert target.repositories() == ("product_root", "api")


def test_a_workspace_target_mounts_exactly_its_selected_members():
    """A rehydrated target is validated without `from_selection` in the path,
    so a selected member with no frozen mount -- or a mount nobody selected --
    is out of the type."""
    mount = {"api": {"repository": "api", "path": "services/api"}}
    with pytest.raises(ValidationError, match="mount"):
        te.WorkItemTarget(
            kind="workspace", workspace="product", members=("api", "web"), mounts=mount
        )
    with pytest.raises(ValidationError, match="mount"):
        te.WorkItemTarget(kind="workspace", workspace="product", members=(), mounts=mount)
    with pytest.raises(ValidationError, match="no workspace or members"):
        te.WorkItemTarget(kind="repository", repository="api", mounts=mount)
    with pytest.raises(ValidationError, match="no workspace or members"):
        te.WorkItemTarget(kind="repository", repository="api", root="api")


def test_workspace_target_rejects_an_unmounted_member(workspace):
    with pytest.raises(te.TemplateEnvironmentError, match="nope"):
        te.WorkItemTarget.from_selection(workspace, members=["nope"])


def test_work_item_target_for_a_single_repository():
    target = te.WorkItemTarget.for_repository("api")
    assert target.kind == "repository"
    assert target.repository == "api"
    assert target.members == ()


def test_work_item_target_is_immutable(workspace):
    target = te.WorkItemTarget.from_selection(workspace, members=["api"])
    with pytest.raises(ValidationError):
        target.members = ("other",)


def test_repository_target_cannot_carry_workspace_fields():
    """Phase 2 freezes this model into a work item and rehydrates it from
    stored JSON, where `for_repository`/`from_selection` are not in the path --
    so the invalid state has to be out of the type, not just unbuilt."""
    with pytest.raises(ValidationError, match="no workspace or members"):
        te.WorkItemTarget(
            kind="repository", repository="api", workspace="product", members=("api",)
        )


def test_repository_target_must_name_a_repository():
    with pytest.raises(ValidationError, match="must name a repository"):
        te.WorkItemTarget(kind="repository")


def test_workspace_target_must_name_a_workspace_and_no_repository():
    with pytest.raises(ValidationError, match="must name a workspace"):
        te.WorkItemTarget(kind="workspace", members=("api",))
    with pytest.raises(ValidationError, match="no repository"):
        te.WorkItemTarget(kind="workspace", workspace="product", repository="api")


def test_work_item_target_round_trips_through_a_decoded_json_dict(workspace):
    """`model_validate_json` relaxes list-to-tuple coercion for `members`, but
    a target stored as one column inside a larger row is decoded with the row
    and validated as a dict -- `json.loads()` then `model_validate(dict)`, not
    `model_validate_json` directly. That path must work too."""
    import json

    target = te.WorkItemTarget.from_selection(workspace, members=["api"])
    decoded = json.loads(json.dumps(target.model_dump(mode="json")))
    rehydrated = te.WorkItemTarget.model_validate(decoded)
    assert rehydrated == target
    assert isinstance(rehydrated.members, tuple)


def test_repository_target_rejects_a_non_default_include_root():
    with pytest.raises(ValidationError, match="no root to include"):
        te.WorkItemTarget(kind="repository", repository="api", include_root=True)


def test_repository_target_include_root_defaults_off():
    target = te.WorkItemTarget.for_repository("api")
    assert target.include_root is False


def test_environment_ids_use_the_same_rule_as_the_references_to_them():
    """A repository id a workspace member (or an `AgentTask.harness`) cannot
    name is a definition nothing can reference."""
    from kraft import config

    with pytest.raises(ValidationError, match="string_pattern_mismatch"):
        config.RepoEntry(id="Api-Service", path="/work/api")
    with pytest.raises(ValidationError, match="string_pattern_mismatch"):
        te.Workspace(id="Product", root="product_root")


# --- `harnesses.yaml` (`from_yaml` does boundary I/O, `from_input` is pure) ---

_REPO_ROOT = Path(__file__).resolve().parents[2]


def test_harness_profiles_load_against_their_provider_declarations(tmp_path):
    """`provider-declares-harness-capabilities`: a profile selects only from
    the provider's own declared surface, checked against `kraft.harness` rather
    than against a second copy of that list kept here."""
    from kraft import harness

    harnesses = harness.load(None).valid
    path = tmp_path / "harnesses.yaml"
    path.write_text(
        "harnesses:\n"
        "  codex:\n"
        "    provider: codex\n"
        "    executable: codex\n"
        "    defaults: { effort: medium }\n"
    )
    table = te.HarnessProfileTable.from_yaml(path, harnesses=harnesses)
    profile = table.profiles["codex"]
    assert (profile.provider, profile.executable) == ("codex", "codex")
    assert profile.defaults == {"effort": "medium"}
    assert profile.is_available()


def test_a_profile_whose_provider_is_not_its_harness_id_is_refused(tmp_path):
    from kraft import harness

    path = tmp_path / "harnesses.yaml"
    path.write_text("harnesses:\n  claude: { provider: not_a_harness }\n")
    with pytest.raises(te.TemplateEnvironmentError, match="is not an installed harness"):
        te.HarnessProfileTable.from_yaml(path, harnesses=harness.load(None).valid)


def test_a_profile_default_the_provider_does_not_declare_is_refused(tmp_path):
    """`harness-profile-has-safe-instance-configuration`: a profile carries
    configuration, never arbitrary command fragments -- so an option the
    provider never declared cannot ride in as one."""
    from kraft import harness

    path = tmp_path / "harnesses.yaml"
    path.write_text(
        "harnesses:\n  gem: { provider: gemini, defaults: { allowed_tools: everything } }\n"
    )
    with pytest.raises(te.TemplateEnvironmentError, match="not a capability"):
        te.HarnessProfileTable.from_yaml(path, harnesses=harness.load(None).valid)


def test_the_seeded_harnesses_yaml_loads_and_covers_the_seeded_library(tmp_path):
    """The seed is the first thing a fresh home runs against: every `harness:`
    the seeded `library.yaml` names must resolve to a profile, or the shipped
    chain cannot dispatch a single agent task."""
    import yaml as _yaml

    from kraft import harness

    table = te.HarnessProfileTable.from_yaml(
        _REPO_ROOT / "templates" / "harnesses.yaml", harnesses=harness.load(None).valid
    )
    library = _yaml.safe_load((_REPO_ROOT / "templates" / "library.yaml").read_text())
    named = {t["harness"] for t in library["tasks"].values() if t.get("harness")}
    assert named and named <= set(table.profiles), sorted(named - set(table.profiles))
