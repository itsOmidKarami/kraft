"""`/openapi.json`, which `/docs` and `/redoc` render and which needs no login
(docs: reference/http-api): it names Kraft and the running version, and its
descriptions, read off the route docstrings, carry no reference only the
maintainer can follow."""

from __future__ import annotations

import re

import pytest
from support.api import _poll_events, _post_default
from support.harness import connect_repo

from kraft import update
from kraft.api import api_router, app
from kraft.api.schemas import WorkItemDetail, WorkItemList

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


def test_every_api_route_has_a_tag_and_a_description():
    bare = [
        f"{method.upper()} {path}"
        for path, ops in app.openapi()["paths"].items()
        if path.startswith("/api/")
        for method, op in ops.items()
        if not (op.get("tags") and op.get("description"))
    ]
    assert bare == []


def test_the_two_stable_routes_are_tagged_stable():
    ops = [
        f"{method.upper()} {path}"
        for path, item in app.openapi()["paths"].items()
        for method, op in item.items()
        if "stable" in op.get("tags", [])
    ]
    assert sorted(ops) == ["GET /api/health", "POST /api/triggers"]


@pytest.mark.parametrize(
    "path, model",
    [
        pytest.param("/api/work-items", WorkItemList, id="list"),
        pytest.param("/api/work-items/{id}", WorkItemDetail, id="detail"),
    ],
)
def test_a_documented_response_shape_matches_the_real_answer(client, repo, path, model):
    """The schema describes a route's answer without filtering it: the model sits under
    `responses`, no route has a `response_model`, and a real answer validates against it."""
    connect_repo(repo)
    wid = _post_default(client, repo)
    _poll_events(client, wid, "gate_requested")
    answer = client.get(path.replace("{id}", wid)).json()
    model.model_validate(answer)
    operation = app.openapi()["paths"][path.replace("{id}", "{wid}")]["get"]
    ref = operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
    assert ref.endswith(f"/{model.__name__}")
    route = next(r for r in api_router.routes if r.path == path.replace("{id}", "{wid}"))
    assert route.response_model is None
