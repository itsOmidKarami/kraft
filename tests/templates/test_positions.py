"""`kraft.templates.positions`: YAML key paths and parser marks as editor positions."""

from __future__ import annotations

import yaml

from kraft.templates import positions
from kraft.templates.library import TemplateIssue

DOC = """\
# a comment
id: demo
nodes:
  - id: build
    kind: exec
    tasks:
      - id: t
        extends: implementer
  - id: ship
    kind: gate
"""


def test_locate_finds_a_mapping_key():
    assert positions.locate(DOC, ("id",)) == (2, 1)


def test_locate_walks_sequences_by_index():
    assert positions.locate(DOC, ("nodes", 1, "kind")) == (10, 5)


def test_locate_points_at_the_key_of_a_nested_entry():
    assert positions.locate(DOC, ("nodes", 0, "tasks", 0, "extends")) == (8, 9)


def test_locate_stops_at_the_deepest_existing_node():
    # `prompt` is not in the file (it would be inherited): the task itself.
    assert positions.locate(DOC, ("nodes", 0, "tasks", 0, "prompt")) == (7, 9)


def test_locate_ignores_an_index_past_the_end():
    assert positions.locate(DOC, ("nodes", 7, "kind")) == (3, 1)


def test_locate_matches_non_string_keys_by_text():
    assert positions.locate("1: one\n2: two\n", (2,)) == (2, 1)


def test_locate_falls_back_to_the_first_line():
    assert positions.locate(DOC, ()) == (1, 1)
    assert positions.locate("", ("id",)) == (1, 1)
    assert positions.locate("nodes: [unclosed\n", ("nodes",)) == (1, 1)


def _mark_of_bad_yaml():
    try:
        yaml.safe_load("a: 1\nb: [unclosed\n")
    except yaml.YAMLError as exc:
        return exc
    raise AssertionError("must not parse")


def test_yaml_mark_reads_the_parser_problem_position():
    exc = _mark_of_bad_yaml()
    assert positions.yaml_mark(exc) == (exc.problem_mark.line + 1, exc.problem_mark.column + 1)


def test_yaml_mark_follows_the_cause_chain():
    exc = _mark_of_bad_yaml()
    try:
        raise ValueError("wrapped") from exc
    except ValueError as wrapped:
        assert positions.yaml_mark(wrapped) == positions.yaml_mark(exc)


def test_yaml_mark_is_none_without_a_yaml_error():
    assert positions.yaml_mark(ValueError("plain")) is None


def test_issue_view_locates_from_disk(tmp_path):
    f = tmp_path / "c.yaml"
    f.write_text(DOC)
    view = positions.issue_view(TemplateIssue(f, "c", "boom", loc=("nodes", 1, "kind")))
    assert (view["line"], view["column"], view["related"]) == (10, 5, None)
    assert view["file"] == str(f) and view["chain"] == "c" and view["message"] == "boom"


def test_issue_view_prefers_a_buffer_over_disk(tmp_path):
    f = tmp_path / "c.yaml"
    f.write_text("id: x\n")
    issue = TemplateIssue(f, "c", "boom", loc=("nodes",))
    assert positions.issue_view(issue, {f: DOC})["line"] == 3


def test_issue_view_uses_the_parser_mark_first(tmp_path):
    issue = TemplateIssue(tmp_path / "gone.yaml", None, "bad", loc=("id",), mark=(4, 2))
    view = positions.issue_view(issue)
    assert (view["line"], view["column"]) == (4, 2)


def test_issue_view_locates_related_in_its_own_file(tmp_path):
    lib = tmp_path / "library.yaml"
    lib.write_text("tasks:\n  broken:\n    kind: subprocess\n    command: 7\n")
    issue = TemplateIssue(
        tmp_path / "c.yaml", "c", "boom", loc=(), related=(lib, ("tasks", "broken", "command"))
    )
    assert positions.issue_view(issue)["related"] == {"file": str(lib), "line": 4, "column": 5}


def test_issue_view_survives_an_unreadable_file(tmp_path):
    view = positions.issue_view(TemplateIssue(tmp_path / "missing.yaml", None, "x", loc=("a",)))
    assert (view["line"], view["column"]) == (1, 1)
