"""`dev/check_llm_docs.py`: the docs the site hands to LLMs carry no dead links.

docs.yml runs it on the built site, which is what proves
docsite/server/plugins/llm-links.ts works. These pin the checker itself, so a
loosened regex cannot turn that step into a pass that checks nothing.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "dev" / "check_llm_docs.py"
BASE = "https://itsomidkarami.github.io/kraft/next"


@pytest.fixture(scope="module")
def cl():
    spec = importlib.util.spec_from_file_location("_dev_check_llm_docs", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_root_relative_finds_markdown_links_images_and_html_attributes(cl):
    page = "\n".join(
        [
            "See [harnesses](/reference/harnesses#grants) and ![x](/diagrams/x.svg).",
            'And <a href="/guides/y">y</a>, <img src="/assets/z.png"> and <u-card to="/a">.',
        ]
    )
    assert [link for _, link in cl.root_relative_links(page)] == [
        "/reference/harnesses#grants",
        "/diagrams/x.svg",
        "/guides/y",
        "/assets/z.png",
        "/a",
    ]


def test_root_relative_ignores_absolute_anchor_protocol_relative_and_relative(cl):
    ok = f"[ok]({BASE}/reference/x) [anchor](#here) [cdn](//cdn.example/x.js) [rel](../x)"
    page = f"{ok}\n[bad](/after)"
    assert cl.root_relative_links(page) == [(2, "/after")]


def test_root_relative_reads_past_a_fence_the_stringifier_glued_to_a_paragraph(cl):
    # Nuxt Content writes "a CI secret.```bash": a fence not at a line start. A
    # check that tracked fences would take the closing one for an opener and
    # stop reading the rest of the page.
    page = "a CI secret.```bash\nkraft admin stop\n```\n\n[bad](/after/the/fence)"
    assert cl.root_relative_links(page) == [(5, "/after/the/fence")]


def test_landing_anchors_flags_a_fragment_the_landing_page_lacks(cl):
    full = f"[a]({BASE}/#credentials) [b]({BASE}/reference/x#credentials) [c]({BASE}/#hero)"
    assert cl.landing_anchors(full, BASE, {"#hero"}) == [f"{BASE}/#credentials"]


def _site(tmp_path: Path, raw: str, full: str, landing: str = "# Kraft\n") -> Path:
    (tmp_path / "raw").mkdir()
    (tmp_path / "raw" / "index.md").write_text(landing)
    (tmp_path / "raw" / "page.md").write_text(raw)
    (tmp_path / "llms-full.txt").write_text(full)
    return tmp_path


def test_check_channel_passes_a_clean_build(cl, tmp_path):
    site = _site(tmp_path, f"[x]({BASE}/reference/x) [here](#there)", f"[x]({BASE}/page#there)")
    assert cl.check_channel(site, BASE) == []


def test_check_channel_reports_each_kind_of_dead_link(cl, tmp_path):
    site = _site(tmp_path, "[x](/reference/x)", f"[x]({BASE}/#there)")
    problems = cl.check_channel(site, BASE)
    assert len(problems) == 2
    assert "raw/page.md:1: root-relative link /reference/x" in problems[0]
    assert f"{BASE}/#there" in problems[1]


def test_check_channel_refuses_a_build_with_no_raw_pages(cl, tmp_path):
    # Zero pages would pass every per-page check; that is a build the check cannot see.
    (tmp_path / "llms-full.txt").write_text("")
    assert cl.check_channel(tmp_path, BASE)


def test_main_checks_both_channels(cl, tmp_path, capsys):
    stable = _site(tmp_path, "", "")
    nxt = tmp_path / "next"
    nxt.mkdir()
    _site(nxt, "[x](/bad)", "")
    assert cl.main(["check", str(stable)]) == 1
    assert "next/raw/page.md:1" in capsys.readouterr().out
