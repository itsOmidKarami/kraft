"""How Kraft writes a chain or library file it saves: a flat forge task key in
its `config:` form, the file's comments and layout kept, the file on disk
copied to `<file>.pre-config` the first time, and never a save refused for
the lift (`kraft-saves-templates-in-the-config-form`)."""

from __future__ import annotations

import pytest
import yaml
from drafts.test_preserve import LIBRARY, TASKS

from kraft import config
from kraft.config import KEPT_ANCHORS, KEPT_NO_BACKUP, KEPT_UNSAFE, PRE_CONFIG, write_template
from kraft.drafts import preserve
from kraft.templates.models import lift_tasks

#: Task 2's shape with a chain id and comment above it, and a `wait` whose
#: `grace_period` moves too.
CHAIN = "# a chain\nid: c\n" + TASKS.replace(
    "        policy:", "        wait: {grace_period: 30s}\n        policy:"
)


def _lifted(text: str) -> dict:
    data = yaml.safe_load(text)
    lift_tasks(data)
    return data


def _has_run(text: str, run: list[str]) -> bool:
    """`run` appears in `text` as consecutive lines, each stripped."""
    lines = [x.strip() for x in text.splitlines()]
    return any(lines[i : i + len(run)] == run for i in range(len(lines) - len(run) + 1))


@pytest.mark.parametrize(
    ("text", "at", "task", "runs"),
    [
        (
            CHAIN,
            ("nodes", 0, "tasks", 0),
            {
                "id": "review",
                "kind": "forge",
                "target": "mr.automated_review",
                "config": {"reviewers": [{"bot": "a"}], "grace_period": "30s"},
                "policy": {"total_time_cap_minutes": 10},
            },
            [
                ["# a chain", "id: c"],
                ["# the node", "- id: feedback"],
                ["# the task", "- id: review"],
                ["# who we wait for", "config:"],
                ["# the sibling", "- id: other"],
                ["policy: {total_time_cap_minutes: 10}"],
            ],
        ),
        (
            LIBRARY,
            ("tasks", "await_review"),
            {
                "kind": "forge",
                "target": "mr.automated_review",
                "policy": {"total_time_cap_minutes": 30},
                "wait": {"polling": {"initial_interval": "30s"}},
                "config": {"grace_period": "60s"},
            },
            [["config:", "grace_period: 60s", "", "# Describes the MR.", "describe_mr:"]],
        ),
    ],
    ids=["a-chain", "the-shipped-library-shape"],
)
def test_a_flat_template_is_saved_in_the_config_form_with_its_comments(
    tmp_path, text, at, task, runs
):
    path = tmp_path / "chain.yaml"
    path.write_text(text)
    written, warning = write_template(path, text)
    assert warning is None
    assert path.read_text() == written
    data = yaml.safe_load(written)
    assert data == _lifted(text)
    assert lift_tasks(data) == []
    for key in at:
        data = data[key]
    assert data == task
    for run in runs:
        assert _has_run(written, run), run
    assert (tmp_path / f"chain.yaml{PRE_CONFIG}").read_text() == text


FLAT = """\
id: c
nodes:
  - id: feedback
    kind: exec
    tasks:
      - id: review
        kind: forge
        target: mr.automated_review
        reviewers: [{bot: a}]
"""
NESTED = FLAT.replace("reviewers: [{bot: a}]", "config: {reviewers: [{bot: a}]}")
ANCHORS = "x-who: &who [{bot: a}]\n" + FLAT.replace("[{bot: a}]", "*who")
MERGE_KEY = FLAT.replace("      - id: review", "      - &base\n        id: review") + (
    "      - <<: *base\n        id: again\n"
)


def _refuse_backup(write_text):
    def write(path, text):
        if str(path).endswith(PRE_CONFIG):
            raise OSError("read-only")
        write_text(path, text)

    return write


def _rewrite_raises(*_a, **_k):
    raise KeyError("x")


@pytest.mark.parametrize(
    ("on_disk", "text", "written", "backup", "warning", "patch"),
    [
        (NESTED, NESTED, "verbatim", None, None, None),
        (FLAT, NESTED, "verbatim", FLAT, None, None),
        (None, ANCHORS, "verbatim", None, KEPT_ANCHORS, None),
        (None, MERGE_KEY, "verbatim", None, KEPT_ANCHORS, None),
        (None, FLAT, "verbatim", None, KEPT_UNSAFE, "rewrite"),
        (None, FLAT, "lifted", None, None, None),
        (FLAT, FLAT, "verbatim", None, KEPT_NO_BACKUP, "backup"),
        (b"\xff", FLAT, "verbatim", None, KEPT_NO_BACKUP, None),
    ],
    ids=[
        "no-flat-key",
        "flat-on-disk-only",
        "anchors",
        "merge-key",
        "rewrite-fails",
        "new-file",
        "backup-unwritable",
        "disk-not-utf8",
    ],
)
def test_a_save_is_never_refused_for_the_lift(
    tmp_path, monkeypatch, on_disk, text, written, backup, warning, patch
):
    path = tmp_path / "chain.yaml"
    if isinstance(on_disk, bytes):
        path.write_bytes(on_disk)
    elif on_disk is not None:
        path.write_text(on_disk)
    if patch == "rewrite":
        monkeypatch.setattr(preserve, "rewrite", _rewrite_raises)
    if patch == "backup":
        monkeypatch.setattr(config, "write_text", _refuse_backup(config.write_text))
    got, why = write_template(path, text)
    assert why == warning
    assert path.read_text() == got
    if written == "verbatim":
        assert got == text
    else:
        assert got != text
        assert yaml.safe_load(got) == _lifted(text)
    copy = tmp_path / f"chain.yaml{PRE_CONFIG}"
    assert (copy.read_text() if copy.exists() else None) == backup


def test_a_second_save_keeps_the_first_backup(tmp_path):
    path = tmp_path / "chain.yaml"
    path.write_text(FLAT)
    write_template(path, FLAT)
    edited = FLAT.replace("{bot: a}", "{bot: b}")
    path.write_text(edited)
    write_template(path, edited)
    assert (tmp_path / f"chain.yaml{PRE_CONFIG}").read_text() == FLAT
