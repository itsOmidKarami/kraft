"""`/openapi.json`, which `/docs` and `/redoc` render and which needs no login
(docs: reference/http-api): it names Kraft and the running version, and its
descriptions, read off the route docstrings, carry no reference only the
maintainer can follow."""

from __future__ import annotations

import re

from kraft import update
from kraft.api import app

#: What a reader of the published API cannot follow: a private tracker id, a
#: section of an unpublished design document, a design decision's number, or a
#: path into the gitignored spec folders.
INTERNAL = re.compile(
    r"Kraft-[a-z0-9]{3,}|UI v2|§|Ruling \d|\bB\d+\b|\bpoint \d|\bdesign \d|docs/superpowers"
)


def _descriptions(spec: dict):
    for path, ops in spec["paths"].items():
        for method, op in ops.items():
            yield f"{method.upper()} {path}", op.get("description") or ""
    for name, schema in spec["components"]["schemas"].items():
        yield f"schema {name}", schema.get("description") or ""
        for field, prop in schema.get("properties", {}).items():
            yield f"schema {name}.{field}", prop.get("description") or ""


def test_the_schema_names_kraft_and_its_version():
    info = app.openapi()["info"]
    assert info["title"] == "Kraft"
    assert info["version"] == update.installed()


def test_no_published_description_cites_an_internal_reference():
    found = {
        where: sorted(set(INTERNAL.findall(text)))
        for where, text in _descriptions(app.openapi())
        if INTERNAL.search(text)
    }
    assert found == {}
