from pathlib import Path

from kraft.intent import Report, Requirement, check, main, parse_collect_output, parse_file, render

TREE = """\
# Intent: gates

## REQ reject-records-note-and-reopens
WHEN a gate is rejected, the system SHALL record the reviewer's note on the
gate row and reopen the row.
enforced-by: tests/test_gates.py::test_reject
origin: docs/superpowers/specs/2026-09-04-sub-project-a-gate-diff-review-design.md

## REQ two-pins
The system SHALL do a thing.
enforced-by: tests/test_gates.py::test_one, tests/test_gates.py::test_two

## REQ no-pin-yet
WHILE an item is paused, the system SHALL NOT advance the chain.
"""


def _write(tmp_path: Path, body: str = TREE) -> Path:
    path = tmp_path / "gates.md"
    path.write_text(body)
    return path


def test_parse_reads_id_text_pins_and_origin(tmp_path):
    reqs = parse_file(_write(tmp_path))

    assert [r.id for r in reqs] == [
        "reject-records-note-and-reopens",
        "two-pins",
        "no-pin-yet",
    ]
    first = reqs[0]
    assert first.text.startswith("WHEN a gate is rejected")
    assert "reopen the row." in first.text
    assert first.enforced_by == ("tests/test_gates.py::test_reject",)
    assert first.origin is not None and first.origin.endswith("design.md")


def test_parse_splits_comma_separated_pins(tmp_path):
    reqs = parse_file(_write(tmp_path))
    assert reqs[1].enforced_by == (
        "tests/test_gates.py::test_one",
        "tests/test_gates.py::test_two",
    )


def test_parse_allows_zero_pins_and_no_origin(tmp_path):
    reqs = parse_file(_write(tmp_path))
    assert reqs[2].enforced_by == ()
    assert reqs[2].origin is None


def test_parse_records_source_line_for_reporting(tmp_path):
    reqs = parse_file(_write(tmp_path))
    # `## REQ reject-...` is the third line of the file.
    assert reqs[0].line == 3


def test_check_flags_a_pin_that_does_not_resolve(tmp_path):
    reqs = parse_file(_write(tmp_path))
    report = check(reqs, {"tests/test_gates.py::test_one", "tests/test_gates.py::test_two"})

    assert [(r.id, missing) for r, missing in report.broken] == [
        ("reject-records-note-and-reopens", "tests/test_gates.py::test_reject")
    ]
    assert report.ok is False


def test_check_lists_unpinned_without_failing(tmp_path):
    reqs = parse_file(_write(tmp_path))
    all_ids = {
        "tests/test_gates.py::test_reject",
        "tests/test_gates.py::test_one",
        "tests/test_gates.py::test_two",
    }
    report = check(reqs, all_ids)

    assert [r.id for r in report.unpinned] == ["no-pin-yet"]
    assert report.broken == []
    assert report.ok is True


def test_check_flags_duplicate_ids(tmp_path):
    body = TREE + "\n## REQ two-pins\nThe system SHALL do it twice.\n"
    reqs = parse_file(_write(tmp_path, body))
    report = check(reqs, set())

    assert [r.id for r in report.duplicates] == ["two-pins"]
    assert report.ok is False


def test_report_is_empty_for_an_empty_tree(tmp_path):
    path = tmp_path / "empty.md"
    path.write_text("# Intent: nothing\n")
    report = check(parse_file(path), set())

    assert report == Report(broken=[], unpinned=[], duplicates=[])
    assert report.ok is True


def test_malformed_req_id_is_reported_not_dropped(tmp_path):
    # A capital, an underscore: fails the kebab-case id rule but still parses
    # as a heading, so it must not vanish along with its text and pins.
    body = (
        "# Intent: gates\n"
        "\n"
        "## REQ ok-one\n"
        "The system SHALL do a thing.\n"
        "enforced-by: tests/test_gates.py::test_x\n"
        "\n"
        "## REQ Bad_ID\n"
        "The system SHALL do another thing.\n"
        "\n"
        "## REQ ok-two\n"
        "The system SHALL do a third thing.\n"
    )
    path = _write(tmp_path, body)
    reqs = parse_file(path)

    # Parsing keeps the malformed requirement rather than silently dropping it.
    assert [r.id for r in reqs] == ["ok-one", "Bad_ID", "ok-two"]

    report = check(reqs, {"tests/test_gates.py::test_x"})

    assert [r.id for r in report.malformed] == ["Bad_ID"]
    assert report.ok is False

    text = render(report)
    assert "MALFORMED" in text
    assert "Bad_ID" in text


def test_check_does_not_flag_duplicate_ids_across_different_files(tmp_path):
    body = "# Intent: a\n\n## REQ shared-id\nThe system SHALL do a thing.\n"
    path_a = tmp_path / "a.md"
    path_a.write_text(body)
    path_b = tmp_path / "b.md"
    path_b.write_text(body.replace("Intent: a", "Intent: b"))

    reqs = parse_file(path_a) + parse_file(path_b)
    report = check(reqs, set())

    assert report.duplicates == []
    assert report.ok is True


def test_requirement_is_hashable_and_frozen(tmp_path):
    req = parse_file(_write(tmp_path))[0]
    assert isinstance(req, Requirement)
    assert {req}  # frozen dataclasses are hashable


COLLECT_OUTPUT = """\
tests/test_gates.py::test_walk_stops_at_first_gate
tests/test_gates.py::test_reject_records_the_note_and_reopen_flips_the_row
tests/test_api.py::TestPerimeter::test_denies_anonymous

3 tests collected in 0.15s
"""


def test_parse_collect_output_keeps_only_node_ids():
    assert parse_collect_output(COLLECT_OUTPUT) == {
        "tests/test_gates.py::test_walk_stops_at_first_gate",
        "tests/test_gates.py::test_reject_records_the_note_and_reopen_flips_the_row",
        "tests/test_api.py::TestPerimeter::test_denies_anonymous",
    }


def test_parse_collect_output_ignores_warnings_and_blank_lines():
    noisy = "warning: something\n\n" + COLLECT_OUTPUT
    assert len(parse_collect_output(noisy)) == 3


def test_render_names_the_file_and_line_of_a_broken_pin(tmp_path):
    reqs = parse_file(_write(tmp_path))
    text = render(check(reqs, set()))

    assert "BROKEN" in text
    assert "gates.md:3" in text
    assert "tests/test_gates.py::test_reject" in text


def test_render_lists_unpinned_requirements(tmp_path):
    reqs = parse_file(_write(tmp_path))
    all_ids = {
        "tests/test_gates.py::test_reject",
        "tests/test_gates.py::test_one",
        "tests/test_gates.py::test_two",
    }
    text = render(check(reqs, all_ids))

    assert "UNPINNED" in text
    assert "no-pin-yet" in text


def test_render_prints_a_summary_line_even_when_everything_resolves(tmp_path):
    # TREE has 3 requirements: one 1-pin, one 2-pin, one with no pin.
    reqs = parse_file(_write(tmp_path))
    all_ids = {
        "tests/test_gates.py::test_reject",
        "tests/test_gates.py::test_one",
        "tests/test_gates.py::test_two",
    }
    text = render(check(reqs, all_ids))

    assert text.splitlines()[-1] == "intent: 3 requirements, 2 pinned, 1 unpinned, 0 broken"


def test_render_summary_counts_broken_pins(tmp_path):
    reqs = parse_file(_write(tmp_path))
    text = render(check(reqs, set()))

    assert text.splitlines()[-1] == "intent: 3 requirements, 2 pinned, 1 unpinned, 3 broken"


def test_main_exits_one_on_a_broken_pin(tmp_path, monkeypatch, capsys):
    _write(tmp_path)
    monkeypatch.setattr("kraft.intent.collect_node_ids", lambda root: set())

    assert main([str(tmp_path)]) == 1
    assert "BROKEN" in capsys.readouterr().out


def test_main_exits_zero_when_only_unpinned(tmp_path, monkeypatch, capsys):
    _write(tmp_path)
    monkeypatch.setattr(
        "kraft.intent.collect_node_ids",
        lambda root: {
            "tests/test_gates.py::test_reject",
            "tests/test_gates.py::test_one",
            "tests/test_gates.py::test_two",
        },
    )

    assert main([str(tmp_path)]) == 0
    assert "UNPINNED" in capsys.readouterr().out


def test_main_exits_zero_when_the_tree_directory_is_missing(tmp_path, capsys):
    assert main([str(tmp_path / "absent")]) == 0
    assert "no intent tree" in capsys.readouterr().out.lower()


def test_main_exits_one_on_a_malformed_req_id(tmp_path, monkeypatch, capsys):
    path = tmp_path / "gates.md"
    path.write_text("# Intent: gates\n\n## REQ Bad_ID\nThe system SHALL do a thing.\n")
    monkeypatch.setattr("kraft.intent.collect_node_ids", lambda root: set())

    assert main([str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "MALFORMED" in out
    assert "Bad_ID" in out
