"""`dev/docs_versions.py`: which tag each published version of the docs is built from.

build_docs_site.sh builds whatever this lists, so a wrong pick is a wrong page
live (or a missing one), and nothing else would notice.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "dev" / "docs_versions.py"


@pytest.fixture(scope="module")
def dv():
    spec = importlib.util.spec_from_file_location("_dev_docs_versions", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


V0 = ["v0.75.0", "v0.76.2"]
V1 = ["v1.0.8", "v1.3.1", "v1.4.0", "v1.5.0rc14"]  # docs from v1.1.0 on; an rc never counts
HAS_DOCS = {"v1.3.1", "v1.4.0", "v1.5.0", "v2.0.0", "v2.1.0", "v3.0.0"}


@pytest.mark.parametrize(
    ("tags", "latest", "expected"),
    [
        pytest.param(
            [*V0, *V1, "v2.0.0", "v2.0.0rc3"],
            None,
            [("latest", "v2.0.0"), ("v1", "v1.4.0"), ("next", "")],
            id="today-v0-has-no-docs-and-an-rc-is-no-release",
        ),
        pytest.param(
            [*V1, "v2.0.0", "v2.1.0", "v3.0.0"],
            None,
            [("latest", "v3.0.0"), ("v2", "v2.1.0"), ("v1", "v1.4.0"), ("next", "")],
            id="a-new-major-freezes-the-one-before-at-its-last-release",
        ),
        pytest.param(
            ["v1.4.0", "v1.5.0", "v2.0.0"],
            None,
            [("latest", "v2.0.0"), ("v1", "v1.5.0"), ("next", "")],
            id="the-highest-tag-with-docs-not-the-highest-tag-of-the-major",
        ),
        pytest.param(
            ["v1.0.8", "v2.0.0"],
            "v1.4.0",
            [("latest", "v1.4.0"), ("next", "")],
            id="a-chosen-latest-hides-the-newer-majors",
        ),
        pytest.param(["v1.5.0rc1", "main"], None, [], id="no-stable-tag-no-versions"),
    ],
)
def test_versions(dv, tags, latest, expected):
    found = dv.versions(tags, HAS_DOCS.__contains__, latest)
    assert [(v["id"], v["tag"]) for v in found] == expected


def test_labels_and_bases_are_what_the_menu_shows(dv):
    found = dv.versions(["v1.4.0", "v2.0.0"], HAS_DOCS.__contains__)
    assert [(v["label"], v["base"]) for v in found] == [
        ("2.0.0 (latest)", "/"),
        ("1.x", "/v1/"),
        ("next", "/next/"),
    ]


def test_main_prints_the_build_order_latest_last_and_json_in_menu_order(dv, monkeypatch, capsys):
    tags = subprocess.CompletedProcess([], 0, "v1.4.0\nv2.0.0\n")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: tags)
    monkeypatch.setattr(dv, "_has_docs", lambda tag: True)
    monkeypatch.delenv("KRAFT_DOCS_STABLE_TAG", raising=False)
    dv.main([])
    build_order = ["next /next/ -", "v1 /v1/ v1.4.0", "latest / v2.0.0"]
    assert capsys.readouterr().out.splitlines() == build_order
    dv.main(["--json"])
    assert [v["id"] for v in json.loads(capsys.readouterr().out)] == ["latest", "v1", "next"]
