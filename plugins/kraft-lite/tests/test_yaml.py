"""The YAML subset reader: what it reads, what it refuses, and that `read` falls
back to it when PyYAML is not installed.

`CORPUS` holds, for each id, a YAML text and the value `yaml.safe_load` gives it.
The plugin's tests run with nothing but pytest, so they only assert the subset
reader against these values; a test in Kraft's own tree compares every row to
`yaml.safe_load` itself."""

from __future__ import annotations

import pytest

import kl_yaml

CORPUS: dict[str, tuple[str, object]] = {
    "block_mapping": ("a: 1\nb: two\n", {"a": 1, "b": "two"}),
    "nested_mapping": (
        "outer:\n  inner:\n    leaf: x\n  other: y\n",
        {"outer": {"inner": {"leaf": "x"}, "other": "y"}},
    ),
    "block_sequence_of_scalars": ("- a\n- 2\n- c d\n", ["a", 2, "c d"]),
    "sequence_of_mappings": (
        "- id: a\n  kind: exec\n- id: b\n  kind: gate\n",
        [{"id": "a", "kind": "exec"}, {"id": "b", "kind": "gate"}],
    ),
    "sequence_under_key_at_the_key_indent": ("a:\n- x\n- y\nb: 1\n", {"a": ["x", "y"], "b": 1}),
    "sequence_under_key_indented": ("a:\n  - x\n  - y\nb: 1\n", {"a": ["x", "y"], "b": 1}),
    "nested_sequences": ("- - a\n  - b\n- - c\n", [["a", "b"], ["c"]]),
    "literal_block": (
        "a: |\n  one\n  two\n\n  three\nb: 1\n",
        {"a": "one\ntwo\n\nthree\n", "b": 1},
    ),
    "literal_keeps_trailing_blank_lines_clipped": (
        "a: |\n  one\n\n\nb: 1\n",
        {"a": "one\n", "b": 1},
    ),
    "literal_whitespace_line_is_content": (
        "a: |\n  one\n    \n  two\nb: 1\n",
        {"a": "one\n  \ntwo\n", "b": 1},
    ),
    "literal_whitespace_line_last_and_stripped": ("a: |-\n  one\n    \n", {"a": "one\n  "}),
    "literal_strip": ("a: |-\n  one\n  two\n", {"a": "one\ntwo"}),
    "literal_keep": ("a: |+\n  one\n\nb: 1\n", {"a": "one\n\n", "b": 1}),
    "literal_more_indented_lines": (
        "a: |\n  def f():\n      return 1\n",
        {"a": "def f():\n    return 1\n"},
    ),
    "literal_in_a_sequence": ("- |\n  one\n  two\n- x\n", ["one\ntwo\n", "x"]),
    "literal_at_end_without_newline": ("a: |\n  one", {"a": "one"}),
    "folded_block": ("a: >\n  one\n  two\n\n  three\n", {"a": "one two\nthree\n"}),
    "folded_strip": (
        "instructions: >-\n  Never signal a process\n  you did not start.\n  Ask first.\nnext: 1\n",
        {"instructions": "Never signal a process you did not start. Ask first.", "next": 1},
    ),
    "folded_whitespace_line_is_content": ("a: >\n  one\n    \n  two\n", {"a": "one\n  \ntwo\n"}),
    "folded_more_indented_keeps_breaks": (
        "a: >\n  one\n    two\n  three\n",
        {"a": "one\n  two\nthree\n"},
    ),
    "flow_sequence": ("a: [a, b]\n", {"a": ["a", "b"]}),
    "flow_sequence_scalars": ("[1, true, x y, 'q', \"r\"]\n", [1, True, "x y", "q", "r"]),
    "flow_sequence_empty": ("a: []\nb: {}\n", {"a": [], "b": {}}),
    "flow_sequence_over_lines": (
        "a: [x,\n  y, # note\n  z]\nb: 1\n",
        {"a": ["x", "y", "z"], "b": 1},
    ),
    "flow_mapping_nested": (
        "wait: {polling: {initial_interval: 30s, max_interval: 5m}}\n",
        {"wait": {"polling": {"initial_interval": "30s", "max_interval": "5m"}}},
    ),
    "flow_mapping_quoted_keys": ("a: {\"k\": 1, 'j': [2]}\n", {"a": {"k": 1, "j": [2]}}),
    "quoted_single": ("a: 'it''s'\n", {"a": "it's"}),
    "quoted_double": ('a: "a\\tb"\n', {"a": "a\tb"}),
    "quoted_double_escapes": ('a: "q\\"\\\\\\n\\x41\\u00e9"\n', {"a": 'q"\\\nAé'}),
    "quoted_with_colon": ('a: "src/**: x"\n', {"a": "src/**: x"}),
    "quoted_key": ("\"src/**\": x\n'b c': y\n", {"src/**": "x", "b c": "y"}),
    "quoted_keeps_hash": ("a: 'x # y'\nb: \"p # q\"  # note\n", {"a": "x # y", "b": "p # q"}),
    "quoted_stays_a_string": ("a: '3'\nb: \"true\"\nc: ''\n", {"a": "3", "b": "true", "c": ""}),
    "apostrophe_in_plain": ("a: it's fine\n", {"a": "it's fine"}),
    "trailing_comment": ("a: 1  # note\n", {"a": 1}),
    "comment_lines_indented": (
        "# top\na:\n  # inside\n  b: 1\n    # deeper, still a comment\n  c: 2\n# last\n",
        {"a": {"b": 1, "c": 2}},
    ),
    "hash_without_space_is_text": (
        "a: x#y\nb: http://h/#frag\n",
        {"a": "x#y", "b": "http://h/#frag"},
    ),
    "ints_bools_null": (
        "a: 3\nb: true\nc: false\nd: null\ne: ~\nf: -7\ng: 0x1f\nh: 1_000\n",
        {"a": 3, "b": True, "c": False, "d": None, "e": None, "f": -7, "g": 31, "h": 1000},
    ),
    "bools_of_yaml_1_1": (
        "a: yes\nb: No\nc: on\nd: OFF\ne: True\n",
        {"a": True, "b": False, "c": True, "d": False, "e": True},
    ),
    "floats": ("a: 1.5\nb: -0.25\nc: 1e3\nd: .5\n", {"a": 1.5, "b": -0.25, "c": "1e3", "d": 0.5}),
    "duration_stays_string": ("a: 30s\nb: 5m\nc: 2h\n", {"a": "30s", "b": "5m", "c": "2h"}),
    "plain_strings_that_look_close": (
        "a: 1.2.3\nb: nullish\nc: -x\nd: 08\n",
        {"a": "1.2.3", "b": "nullish", "c": "-x", "d": "08"},
    ),
    "colon_without_space_is_text": ("a: x:y\nb: c:\\d\n", {"a": "x:y", "b": "c:\\d"}),
    "empty_value_is_null": ("a:\n", {"a": None}),
    "empty_value_then_sibling": ("a:\nb: 1\n", {"a": None, "b": 1}),
    "empty_sequence_item": ("- \n- x\n", [None, "x"]),
    "int_and_null_keys": ("1: a\nnull: c\n", {1: "a", None: "c"}),
    "duplicate_key_last_wins": ("a: 1\na: 2\n", {"a": 2}),
    "dollar_and_backticks_in_block": (
        'run: |\n  echo "$KRAFT_DAEMON_PID" `date` $(pwd)\n  # not a comment\n',
        {"run": 'echo "$KRAFT_DAEMON_PID" `date` $(pwd)\n# not a comment\n'},
    ),
    "empty_document": ("", None),
    "only_comments": ("# nothing here\n\n", None),
    "leading_document_marker": ("---\na: 1\n", {"a": 1}),
    "crlf_line_endings": ("a: 1\r\nb:\r\n  - x\r\n", {"a": 1, "b": ["x"]}),
    "no_trailing_newline": ("a: 1", {"a": 1}),
    "top_level_scalar": ("hello\n", "hello"),
    "indented_document": ("  a: 1\n  b: 2\n", {"a": 1, "b": 2}),
}


@pytest.mark.parametrize("text, expected", CORPUS.values(), ids=list(CORPUS))
def test_subset_reads(text, expected):
    assert kl_yaml.load(text) == expected


#: Each id is the construct, and `want` the words its message must carry beside
#: `<string>:<line>`.
REFUSALS: dict[str, tuple[str, int, str]] = {
    "anchor": ("a: &x 1\n", 1, "anchor"),
    "anchor_on_a_sequence_item": ("- &x a\n", 1, "anchor"),
    "alias": ("b: *x\n", 1, "alias"),
    "tag": ("a: !!str 1\n", 1, "tag"),
    "multi_document": ("---\na: 1\n---\nb: 2\n", 3, "document"),
    "document_end_marker": ("a: 1\n...\n", 2, "document"),
    "tab_indent": ("a:\n\tb: 1\n", 2, "tab"),
    "complex_key": ("? a\n", 1, "complex key"),
    "bad_indent": ("a:\n    b: 1\n  c: 2\n", 3, "indent"),
    "stray_deeper_line": ("a: 1\n  b: 2\n", 2, "indent"),
    "sequence_item_in_a_mapping": ("a: 1\n- b\n", 2, "sequence"),
    "second_root": ("- a\nb: 1\n", 2, "indent"),
    "multiline_quoted": ('a: "x\n  y"\n', 1, "quoted"),
    "unterminated_flow": ("a: [x, y\nb: 1\n", 1, "flow"),
    "unterminated_quote": ("a: 'x\n", 1, "quoted"),
    "missing_colon": ("a: 1\nb\n", 2, "mapping"),
    "nested_inline_mapping": ("a: b: c\n", 1, "mapping"),
    "unknown_escape": ('a: "\\q"\n', 1, "escape"),
    "bad_block_header": ("a: |x\n  y\n", 1, "block scalar"),
    "merge_key_flow": ("a: 1\n<<: {b: 2}\n", 2, "merge key"),
    "merge_key_block": ("<<:\n  b: 2\n", 1, "merge key"),
    "merge_key_sequence": ("- x: 1\n  <<: [{b: 2}]\n", 2, "merge key"),
    "merge_key_in_flow_mapping": ("a: {<<: {b: 2}}\n", 1, "merge key"),
    "value_tag_scalar": ("a: =\n", 1, "`=`"),
    "block_scalar_leading_spaces_line": ("a: |\n    \n  one\n", 2, "indent"),
    "timestamp": ("a: 2024-01-02\n", 1, "timestamp"),
    "sexagesimal": ("a: 1:30\n", 1, "sexagesimal"),
}


@pytest.mark.parametrize("text, line, want", REFUSALS.values(), ids=list(REFUSALS))
def test_subset_refuses(text, line, want):
    with pytest.raises(kl_yaml.YamlError) as caught:
        kl_yaml.load(text)
    message = str(caught.value)
    assert message.startswith("kraft-lite: ")
    assert f"<string>:{line}" in message
    assert want in message


def test_refusal_names_its_source():
    with pytest.raises(kl_yaml.YamlError, match=r"chains/x\.yaml:2"):
        kl_yaml.load("a: 1\nb: *x\n", source="chains/x.yaml")


def test_yaml_error_is_a_clean_exit():
    assert issubclass(kl_yaml.YamlError, SystemExit)


def test_read_uses_the_subset_reader_without_pyyaml(tmp_path, monkeypatch):
    monkeypatch.setattr(kl_yaml, "_safe_load", None)
    path = tmp_path / "x.yaml"
    path.write_text("a: [1, 2]\nb: >-\n  folded\n  text\n")
    assert kl_yaml.read(path) == {"a": [1, 2], "b": "folded text"}


@pytest.mark.parametrize("backend", ["subset", "pyyaml"])
def test_read_empty_file_is_none(tmp_path, monkeypatch, backend):
    if backend == "subset":
        monkeypatch.setattr(kl_yaml, "_safe_load", None)
    elif kl_yaml._safe_load is None:
        pytest.skip("PyYAML is not installed")
    path = tmp_path / "empty.yaml"
    path.write_text("")
    assert kl_yaml.read(path) is None


def test_read_names_the_file_in_a_refusal(tmp_path, monkeypatch):
    monkeypatch.setattr(kl_yaml, "_safe_load", None)
    path = tmp_path / "x.yaml"
    path.write_text("a: &x 1\n")
    with pytest.raises(kl_yaml.YamlError, match=rf"{path.name}:1"):
        kl_yaml.read(path)


def test_read_turns_a_pyyaml_error_into_a_yaml_error(tmp_path):
    if kl_yaml._safe_load is None:
        pytest.skip("PyYAML is not installed")
    path = tmp_path / "x.yaml"
    path.write_text("a: [unterminated\n")
    with pytest.raises(kl_yaml.YamlError, match=r"^kraft-lite: .*x\.yaml"):
        kl_yaml.read(path)
