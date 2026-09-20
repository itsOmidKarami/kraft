import pytest
from pydantic import ValidationError

from kraft import template_environment as te


@pytest.fixture
def api_repository() -> te.Repository:
    return te.Repository(
        id="api",
        path="/work/product/services/api",
        forge=te.ForgeTarget(kind="github", project="acme/api"),
        worktree=te.Worktree(
            setup="just setup",
            local_files=[".env.test"],
            environment=te.WorktreeEnvironment(set={"CI": "1"}, pass_through=["NPM_TOKEN"]),
        ),
        verification=te.Verification(
            test_scopes=[te.TestScope(paths=["src/**", "tests/**"], command="just test")]
        ),
    )


@pytest.fixture
def workspace() -> te.Workspace:
    return te.Workspace(
        id="product",
        root="product_root",
        root_pointer_default="ignore",
        members={"api": te.WorkspaceMember(repository="api", path="services/api")},
    )


# ── repositories, workspaces, areas are distinct
# (repositories-workspaces-and-areas-are-distinct) ──


def test_repository_parses_worktree_and_verification(api_repository):
    assert api_repository.forge == te.ForgeTarget(kind="github", project="acme/api")
    assert api_repository.worktree.setup == "just setup"
    assert api_repository.worktree.local_files == [".env.test"]
    assert api_repository.worktree.environment.pass_through == ["NPM_TOKEN"]
    assert api_repository.verification.test_scopes[0].command == "just test"


def test_repository_worktree_rejects_an_unsafe_local_file():
    with pytest.raises(ValidationError, match="local_files"):
        te.Repository(id="api", path="/work/api", worktree=te.Worktree(local_files=["../secret"]))


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


def test_repository_with_areas_keeps_them_path_scoped_not_independent():
    platform = te.Repository(
        id="platform",
        path="/work/platform",
        areas={
            "python_api": te.Area(paths=["services/api/**"], setup="uv sync"),
            "java_worker": te.Area(paths=["services/worker/**"], setup="./gradlew classes"),
        },
    )
    assert set(platform.areas) == {"python_api", "java_worker"}
    assert platform.areas["python_api"].paths == ["services/api/**"]


# ── workspaces (workspace-declares-root-and-members) ──


def test_workspace_declares_root_and_members(workspace):
    assert workspace.root == "product_root"
    assert workspace.members["api"].path == "services/api"
    assert workspace.root_pointer_default == te.RootPointerPolicy.IGNORE


def test_workspace_target_captures_selected_members(workspace):
    assert te.WorkItemTarget.from_selection(
        workspace, members=["api"], include_root=False
    ).members == ("api",)


def test_workspace_target_rejects_an_unmounted_member(workspace):
    with pytest.raises(te.TemplateEnvironmentError, match="nope"):
        te.WorkItemTarget.from_selection(workspace, members=["nope"])


def test_work_item_target_for_a_single_repository(api_repository):
    target = te.WorkItemTarget.for_repository(api_repository)
    assert target.kind == "repository"
    assert target.repository == "api"
    assert target.members == ()


def test_work_item_target_is_immutable(workspace):
    target = te.WorkItemTarget.from_selection(workspace, members=["api"])
    with pytest.raises(ValidationError):
        target.members = ("other",)


# ── harness profiles: provider-defined runtime option validation ──


class _FakeHarness:
    """A stand-in for `kraft.harness.Harness` -- only `supports`/`value_ok`
    matter to `HarnessProfile.from_input`."""

    id = "fake"

    def supports(self, name: str) -> bool:
        return name in ("model", "effort")

    def value_ok(self, name: str, value: str) -> bool:
        return name != "effort" or value in ("low", "medium", "high")


def test_harness_profile_selects_only_declared_options():
    parsed = te.HarnessProfileInput(
        provider="codex", executable="codex", defaults={"effort": "medium"}
    )
    profile = te.HarnessProfile.from_input("codex_default", parsed, harness=_FakeHarness())
    assert profile.is_available() is True
    assert profile.executable == "codex"


def test_harness_profile_rejects_an_undeclared_option():
    parsed = te.HarnessProfileInput(provider="codex", defaults={"network": "on"})
    with pytest.raises(te.TemplateEnvironmentError, match="network"):
        te.HarnessProfile.from_input("codex_default", parsed, harness=_FakeHarness())


def test_harness_profile_rejects_a_value_the_provider_does_not_accept():
    parsed = te.HarnessProfileInput(provider="codex", defaults={"effort": "extreme"})
    with pytest.raises(te.TemplateEnvironmentError, match="extreme"):
        te.HarnessProfile.from_input("codex_default", parsed, harness=_FakeHarness())


def test_harness_profile_is_unavailable_when_disabled():
    parsed = te.HarnessProfileInput(provider="codex", enabled=False)
    profile = te.HarnessProfile.from_input("codex_default", parsed, harness=_FakeHarness())
    assert profile.is_available() is False
