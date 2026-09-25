"""`kraft.config_schemas`: the committed JSON Schemas are the models' own, in
the form an author writes, and accept every shipped config file."""

from __future__ import annotations

from pathlib import Path

import jsonschema
import pytest
import yaml

from kraft import config_schemas

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "templates"


def validator(name: str) -> jsonschema.Draft202012Validator:
    return jsonschema.Draft202012Validator(config_schemas.SCHEMAS[name]())


@pytest.mark.parametrize("name", sorted(config_schemas.SCHEMAS))
def test_the_committed_schema_is_current(name):
    committed = ROOT / config_schemas.SCHEMA_DIR / name
    assert committed.read_text() == config_schemas.render(name), (
        f"{committed} is stale: run `just schemas`"
    )


@pytest.mark.parametrize("name", sorted(config_schemas.SCHEMAS))
def test_every_schema_is_itself_valid(name):
    assert jsonschema.Draft202012Validator.check_schema(config_schemas.SCHEMAS[name]()) is None


@pytest.mark.parametrize(
    "chain", sorted((TEMPLATES / "chains").glob("*.yaml")), ids=lambda p: p.name
)
def test_every_shipped_chain_validates(chain):
    assert not list(validator("chain.schema.json").iter_errors(yaml.safe_load(chain.read_text())))


@pytest.mark.parametrize(
    ("file", "schema"),
    [
        ("library.yaml", "library.schema.json"),
        ("policy.yaml", "policy.schema.json"),
        ("harnesses.yaml", "harnesses.schema.json"),
        ("repos.yaml", "repos.schema.json"),
        ("intake.yaml", "intake.schema.json"),
    ],
)
def test_every_shipped_config_file_validates(file, schema):
    data = yaml.safe_load((TEMPLATES / file).read_text()) or {}
    assert not list(validator(schema).iter_errors(data))


def test_a_task_that_only_extends_is_valid_in_authored_form():
    chain = {
        "id": "c",
        "nodes": [{"id": "n", "kind": "exec", "tasks": [{"id": "t", "extends": "implementer"}]}],
    }
    assert not list(validator("chain.schema.json").iter_errors(chain))


def test_a_misspelled_key_is_refused():
    chain = {"id": "c", "nodes": [{"id": "n", "kind": "exec", "taks": []}]}
    assert list(validator("chain.schema.json").iter_errors(chain))


def test_a_misspelled_library_section_is_refused():
    assert list(validator("library.schema.json").iter_errors({"taks": {}}))


def test_every_extendable_model_is_in_the_chain_defs():
    defs = config_schemas.SCHEMAS["chain.schema.json"]()["$defs"]
    assert config_schemas.EXTENDABLE <= set(defs)
    for name in config_schemas.EXTENDABLE:
        assert "extends" in defs[name]["properties"]


def test_a_field_named_required_survives_the_authored_transform():
    schema = {
        "type": "object",
        "properties": {"required": {"type": "boolean"}},
        "required": ["required"],
    }
    out = config_schemas.authored(schema)
    assert out["properties"]["required"] == {"type": "boolean"}
    assert "required" not in out
