"""`kraft admin acp-probe`: the conformance probe as the operator runs it (spec §9)."""

import json
import subprocess
import sys

import pytest
import yaml
from support.acp import PROBE_CALLS, PROBE_ENTRY
from support.server import child_env

UNASKED = [*PROBE_CALLS[:2], {**PROBE_CALLS[2], "ask": False}]


def run_verb(tmp_path, *args, calls=PROBE_CALLS):
    return subprocess.run(
        [sys.executable, "-m", "kraft", "admin", "acp-probe", *args],
        capture_output=True,
        text=True,
        timeout=120,
        env=child_env(
            {
                "HOME": str(tmp_path),
                "KRAFT_HOME": str(tmp_path / "k"),
                "KRAFT_FAKE_ACP": "",
                "KRAFT_FAKE_ACP_CALLS": json.dumps(calls),
            }
        ),
    )


def entry_file(tmp_path, text=None):
    path = tmp_path / "fake.yaml"
    path.write_text(yaml.safe_dump(PROBE_ENTRY) if text is None else text)
    return str(path)


@pytest.mark.parametrize(
    ("calls", "code"), [(PROBE_CALLS, 0), (UNASKED, 1)], ids=["conforms", "a-call-ran-unasked"]
)
def test_the_verb_exits_one_on_a_mismatch(tmp_path, calls, code):
    done = run_verb(tmp_path, "--file", entry_file(tmp_path), "--json", calls=calls)
    assert done.returncode == code, done.stdout + done.stderr
    report = json.loads(done.stdout)
    assert report["ok"] is (code == 0) and (report["mismatches"] == []) is (code == 0)
    assert report["agent"] == "fake-acp-agent 0.0.0"


def test_the_human_report_names_each_call_and_what_it_found(tmp_path):
    done = run_verb(tmp_path, "--file", entry_file(tmp_path), calls=UNASKED)
    assert "fake-acp-agent 0.0.0" in done.stdout
    assert "Read" in done.stdout and "asked" in done.stdout
    assert "MISMATCH" in done.stdout and "Bash ran without asking" in done.stdout


@pytest.mark.parametrize(
    ("args", "file_text", "said"),
    [
        (["claude"], None, "kind: acp"),
        (["no-such-harness"], None, "no-such-harness"),
        ([], None, "name a harness"),
        (["--file"], "a: [unclosed", "cannot load"),
        (["--file"], "id: x", "cannot load"),
    ],
    ids=["a-bundled-cli-harness", "unknown-id", "nothing-named", "malformed-yaml", "not-an-entry"],
)
def test_a_harness_that_cannot_be_probed_is_refused_with_the_reason(
    tmp_path, args, file_text, said
):
    if file_text is not None:
        args = [*args, entry_file(tmp_path, file_text)]
    done = run_verb(tmp_path, *args)
    assert done.returncode != 0 and said in done.stderr
    assert "Traceback" not in done.stderr
