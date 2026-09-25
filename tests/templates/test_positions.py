"""`kraft.templates.positions`: YAML key paths and parser marks as editor positions."""

from __future__ import annotations

import yaml

from kraft.templates import positions

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
