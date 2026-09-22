import logging

import pytest
import yaml
from support.harness import entry_of

from kraft import config
from kraft.templates.library import TemplateLibrary
from kraft.worker import steering


def test_the_budget_measures_the_block_a_launch_injects(tmp_path):
    """Kraft-5d510.3: `Steering.block` is both what a launch's context carries
    and what `check_budget` measures, separators included, so the two cannot
    drift. Two bodies filling the budget exactly pass; one byte more fails."""
    fixed = len(steering.Steering.block(["", ""]).encode())
    half = (steering.Steering.MAX_BYTES - fixed) // 2
    bodies = ["x" * half, "y" * (steering.Steering.MAX_BYTES - fixed - half)]
    assert len(steering.Steering.block(bodies).encode()) == steering.Steering.MAX_BYTES
    steering.Steering.check_budget(bodies, where="x")
    with pytest.raises(steering.SteeringError, match="x: steering totals 8193 bytes"):
        steering.Steering.check_budget([*bodies[:1], bodies[1] + "z"], where="x")


def test_select_returns_the_named_profiles_in_order():
    profiles = {"a": "alpha", "b": "beta"}
    assert list(steering.select(["b", "a"], profiles, where="x").values()) == ["beta", "alpha"]


def test_select_refuses_a_name_the_library_does_not_define_naming_where_it_lives():
    with pytest.raises(
        steering.SteeringError, match=r"'nope' is not a steering profile in .*library"
    ):
        steering.select(["nope"], {"a": "alpha"}, where="repos.yaml: /r")


def test_select_refuses_profiles_over_the_budget_together():
    half = "x" * (steering.Steering.MAX_BYTES // 2)
    with pytest.raises(steering.SteeringError, match="8192"):
        steering.select(["a", "b"], {"a": half, "b": half}, where="x")


def test_a_frozen_snapshot_answers_whatever_the_live_library_says():
    entry = entry_of({"path": "/r", "steering": ["house"]})
    frozen = {"/r": {"house": "filed with"}}
    assert steering.for_repository(entry, frozen, {"house": "edited since"}) == ("filed with",)
    # A repository that named nothing at intake gets nothing, whatever it names now.
    assert steering.for_repository(entry, {}, {"house": "edited since"}) == ()


def test_frozen_steering_survives_the_repos_yaml_path_changing():
    """Kraft-jzdyp: repos.yaml's `path` for a connected repo can be hand-
    edited while an item is in flight. The frozen map is still keyed by
    whatever the path was at intake, so a lookup keyed only by the *live*
    entry (whose `.path` now differs) must not be the only way in -- the
    item's own repo, as recorded at intake, has to work too."""
    entry = entry_of({"path": "/new/path", "steering": ["house"]})
    frozen = {"/old/path": {"house": "filed with"}}
    assert steering.for_repository(entry, frozen, None, item_repo="/old/path") == ("filed with",)


def test_an_unsteered_repository_in_a_partly_steered_workspace_gets_nothing_and_does_not_raise():
    """Kraft-jzdyp regression: `deps.repository_steering` only adds repos
    that declare `steering:`, so a workspace item with one steered repo and
    one unsteered one has a non-empty `frozen` that still lacks the
    unsteered repository's own key. That must answer `()`, on the item's
    first dispatch, for both an unsteered root and an unsteered member --
    never raise just because *some other* repository in the item is steered."""
    frozen = {"/steered": {"house": "filed with"}}
    # The unsteered repository is the item's own root: item_repo is its path,
    # simply absent from frozen.
    unsteered_root = entry_of({"path": "/unsteered", "steering": []})
    assert steering.for_repository(unsteered_root, frozen, None, item_repo="/unsteered") == ()
    # The unsteered repository is a fanned-out member: no item_repo, looked
    # up by its own (live) path, also absent from frozen.
    unsteered_member = entry_of({"path": "/unsteered-member", "steering": []})
    assert steering.for_repository(unsteered_member, frozen, None) == ()


def test_a_snapshot_from_before_the_freeze_reads_the_live_library():
    """An rc item in flight across the upgrade read its steering files at
    each launch; they are library profiles now, so it reads those, and stops
    naming the profile the library lacks."""
    entry = entry_of({"path": "/r", "steering": ["house"]})
    assert steering.for_repository(entry, None, {"house": "migrated"}) == ("migrated",)
    with pytest.raises(steering.SteeringError, match="filed before repository steering"):
        steering.for_repository(entry, None, {})


# -- the one-time migration of templates/steering/*.md ------------------------

_LIBRARY = """\
# the operator's own comment
steering:
  kept:
    instructions: the library's own text
"""


def _home(tmp_path, library=_LIBRARY, files=None):
    (tmp_path / "library.yaml").write_text(library)
    d = tmp_path / "steering"
    d.mkdir()
    for name, body in (files or {}).items():
        (d / f"{name}.md").write_text(body)
    return tmp_path


def _steering_of(home):
    return yaml.safe_load((home / "library.yaml").read_text())["steering"]


def test_each_steering_file_becomes_a_library_profile_and_the_directory_moves_aside(tmp_path):
    body = "Prefer the stdlib.\n\n  - indented line\n"
    home = _home(tmp_path, files={"house-style": body, "kept": "the file's text"})
    (home / "repos.yaml").write_text(
        yaml.safe_dump({"repos": [{"path": "/r", "steering": ["house-style", "kept"]}]})
    )

    assert steering.migrate_files(home) == ["house-style"]

    assert _steering_of(home) == {
        # A name the library already defines keeps the library's text.
        "kept": {"instructions": "the library's own text"},
        "house-style": {"instructions": body},
    }
    text = (home / "library.yaml").read_text()
    assert text.startswith("# the operator's own comment\n")
    assert not (home / "steering").exists()
    assert (home / "steering.pre-1.0" / "kept.md").read_text() == "the file's text"
    # repos.yaml's names resolve, to the text the files held.
    library = TemplateLibrary.from_yaml_dir(home)
    profiles = {n: p.instructions for n, p in library.steering.items()}
    (entry,) = config.load_repos(home / "repos.yaml", steering=profiles)
    assert steering.select(entry.steering, profiles, where="x")["house-style"] == body
    # Once: the next start finds no directory and does nothing.
    assert steering.migrate_files(home) == []
    assert (home / "library.yaml").read_text() == text


def test_a_library_with_no_steering_section_gains_one(tmp_path):
    home = _home(tmp_path, library="# mine\n", files={"a": "alpha\n"})
    steering.migrate_files(home)
    assert _steering_of(home) == {"a": {"instructions": "alpha\n"}}
    assert (home / "library.yaml").read_text().startswith("# mine\n")


def test_an_unusual_layout_is_rewritten_whole_with_the_original_kept(tmp_path):
    home = _home(tmp_path, library="steering: {x: {instructions: y}}\n", files={"a": "alpha"})
    steering.migrate_files(home)
    assert _steering_of(home) == {"x": {"instructions": "y"}, "a": {"instructions": "alpha"}}
    assert (home / "library.yaml.pre-1.0").read_text() == "steering: {x: {instructions: y}}\n"


def test_an_empty_file_is_skipped_and_kept_aside(tmp_path, caplog):
    home = _home(tmp_path, files={"blank": "  \n"})
    with caplog.at_level(logging.WARNING, logger="kraft.worker.steering"):
        assert steering.migrate_files(home) == []
    assert "blank" not in _steering_of(home)
    assert (home / "steering.pre-1.0" / "blank.md").exists()
    assert "skipped empty" in caplog.text


def test_a_library_that_does_not_parse_leaves_the_files_in_place(tmp_path):
    home = _home(tmp_path, library="steering: [not, a, mapping]\n", files={"a": "alpha"})
    assert steering.migrate_files(home) == []
    assert (home / "steering" / "a.md").exists()
