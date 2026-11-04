"""`yamlload.safe_load`: libyaml's read of a file the operator owns, which must
hand back what `yaml.safe_load` would and fail with the words it would use."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from kraft import yamlload

_ROOT = Path(__file__).resolve().parents[1]
_SHIPPED = sorted(
    p for pattern in ("config/**/*.yaml", "src/kraft/harnesses/*.yaml") for p in _ROOT.glob(pattern)
)

_DOCUMENTS = {
    "scalars": "a: 1\nb: 0o17\nc: yes\nd: 2001-12-14\ne: 1.5e3\nf: ~\n",
    "anchor-and-merge": "base: &b {x: 1, y: 2}\nuse:\n  <<: *b\n  y: 3\n",
    "block-scalars": "text: |\n  one\n  two\nfolded: >\n  a\n  b\n",
    "unicode": "name: 'héllo ✓'\n",
    "empty": "",
    "comment-only": "# nothing here\n",
}

_BROKEN = {
    "mapping-in-mapping": "a: b: c\n",
    "unclosed-flow": "a: [1, 2\n",
    "tab-indent": "a:\n\tb: 1\n",
    "two-documents": "a: 1\n---\nb: 2\n",
}


def test_every_file_kraft_ships_reads_as_it_always_did():
    assert _SHIPPED
    for path in _SHIPPED:
        text = path.read_text()
        assert yamlload.safe_load(text) == yaml.safe_load(text), path


@pytest.mark.parametrize("text", _DOCUMENTS.values(), ids=_DOCUMENTS)
def test_a_document_reads_as_safe_load_reads_it(text):
    assert yamlload.safe_load(text) == yaml.safe_load(text)


@pytest.mark.parametrize("text", _BROKEN.values(), ids=_BROKEN)
def test_a_document_libyaml_refuses_fails_with_the_words_a_person_has_always_seen(text):
    with pytest.raises(yaml.YAMLError) as old:
        yaml.safe_load(text)
    with pytest.raises(yaml.YAMLError) as new:
        yamlload.safe_load(text)
    assert str(new.value) == str(old.value)


@pytest.mark.skipif(not yaml.__with_libyaml__, reason="PyYAML was built without libyaml")
def test_it_parses_with_libyaml_where_pyyaml_has_it(monkeypatch):
    loaders = []
    real = yaml.load

    def spy(text, loader):
        loaders.append(loader)
        return real(text, loader)

    monkeypatch.setattr(yaml, "load", spy)

    assert yamlload.safe_load("a: 1\n") == {"a": 1}
    assert loaders == [yaml.CSafeLoader]


@pytest.mark.parametrize(
    "document",
    ["[" * 100_000 + "]" * 100_000, "- " * 100_000 + "x", "{a: " * 100_000 + "1" + "}" * 100_000],
    ids=["flow-sequences", "block-sequences", "flow-mappings"],
)
def test_nesting_deep_enough_to_crash_libyaml_fails_as_it_always_did(document):
    """libyaml's composer overflows the C stack at ~20,000 levels and kills the process; the
    pure-Python parser raises RecursionError. Run apart: a crash fails the test, not the worker."""
    code = (
        "import sys\nfrom kraft import yamlload\n"
        "try:\n    yamlload.safe_load(sys.stdin.read())\n"
        "except RecursionError:\n    print('RecursionError')\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", code], input=document, capture_output=True, text=True
    )
    assert (done.returncode, done.stdout.strip()) == (0, "RecursionError")


def _packaged_harnesses(tmp_path):
    from kraft.addons import builtin

    builtin._harnesses_memo = None
    builtin._packaged_harnesses()


def _drift_full_copies(tmp_path):
    from kraft.addons import drift

    (tmp_path / "chains").mkdir()
    (tmp_path / "chains" / "mine.yaml").write_text("id: mine\nnodes: []\n")
    drift._full_copies(tmp_path)


@pytest.mark.skipif(not yaml.__with_libyaml__, reason="PyYAML was built without libyaml")
@pytest.mark.parametrize(
    "read",
    [_packaged_harnesses, _drift_full_copies],
    ids=["packaged-harnesses", "operators-chains-in-the-drift-check"],
)
def test_a_server_start_reads_kraft_and_the_operators_own_files_with_libyaml(
    tmp_path, monkeypatch, read
):
    from kraft.addons import builtin

    monkeypatch.setattr(builtin, "_harnesses_memo", None)
    loaders = []
    real = yaml.load
    monkeypatch.setattr(
        yaml, "load", lambda text, loader: loaders.append(loader) or real(text, loader)
    )

    read(tmp_path)

    assert loaders and set(loaders) == {yaml.CSafeLoader}
