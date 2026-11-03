"""`yamlload.safe_load`: libyaml's read of a file the operator owns, which must
hand back what `yaml.safe_load` would and fail with the words it would use."""

from __future__ import annotations

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
