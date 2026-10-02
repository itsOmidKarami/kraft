"""An operator's per-item override is held to one shape per kind: a work
item's own model/effort (`agent_overrides`) and a node's (`node_overrides`)."""

import pytest

from kraft import overrides


def test_validate_agent_overrides_rejects_unknown_effort():
    errs = overrides.validate_agent_overrides({"effort": "turbo"})
    assert errs and "effort" in errs[0]


def test_validate_agent_overrides_rejects_non_string_model():
    """The exact message, which the PATCH route returns as its 422."""
    errs = overrides.validate_agent_overrides({"model": 5})
    assert errs == ["agent_overrides 'model' must be a string or null"]


def test_validate_agent_overrides_rejects_an_unknown_key():
    errs = overrides.validate_agent_overrides({"temperature": 1})
    assert errs and "unknown key(s) ['temperature']" in errs[0]


def test_validate_agent_overrides_accepts_a_partial_object():
    assert overrides.validate_agent_overrides({"model": "opus"}) == []


def test_node_override_schema_rejects_unknown_fields():
    errs = overrides.validate_node_override_fields({"not_a_setting": True})
    assert errs == ["cannot override ['not_a_setting']"]


def test_node_override_schema_rejects_explicit_invalid_bounds():
    assert overrides.validate_node_override_fields({"attempts": 0}) == [
        "attempts must be a positive int"
    ]
    assert overrides.validate_node_override_fields({"auto_escalate_delay_s": -1}) == [
        "auto_escalate_delay_s must be a non-negative int"
    ]


def test_node_override_accepts_explicit_nulls_for_the_model_fields():
    assert overrides.validate_node_override_fields({"model": None, "effort": "high"}) == []


def test_node_override_takes_an_extra_prompt_string_and_nothing_else():
    """Kraft-a7ers."""
    assert overrides.validate_node_override_fields({"extra_prompt": "Mind the migration."}) == []
    assert overrides.validate_node_override_fields({"extra_prompt": 3}) == [
        "'extra_prompt' must be a string or null"
    ]


@pytest.mark.parametrize(
    ("check", "fields"),
    [
        (overrides.validate_agent_overrides, {"model": None, "effort": None}),
        (
            overrides.validate_node_override_fields,
            {"attempts": None, "effort": None, "auto_escalate_stuck": None},
        ),
    ],
    ids=["agent", "node"],
)
def test_a_null_field_is_a_drop_and_is_not_checked_against_its_type(check, fields):
    """A PATCH sends `null` to drop one field (`store.merge_fields`)."""
    assert check(fields) == []


def test_a_null_unknown_field_is_still_named():
    assert overrides.validate_node_override_fields({"not_a_setting": None}) == [
        "cannot override ['not_a_setting']"
    ]


@pytest.mark.parametrize(
    "model",
    [
        "opus",
        "claude-sonnet-4-5",
        "gpt-5.6-sol",
        "us.anthropic.claude-opus:0",
        "openrouter/x",
        "opus[1m]",
        "~anthropic/claude-latest",
        pytest.param("x" * 128, id="128-characters"),
    ],
)
def test_a_model_id_is_accepted(model):
    assert overrides.validate_agent_overrides({"model": model}) == []
    assert overrides.validate_node_override_fields({"escalate_model": model}) == []


@pytest.mark.parametrize(
    "model", ["not a model; rm -rf", "", "-opus", "x" * 129, "opus\n", "~", "~~opus", "op~us"]
)
def test_text_that_is_no_model_id_is_refused_naming_the_rule(model):
    errs = overrides.validate_agent_overrides({"model": model})
    assert len(errs) == 1 and "is not a model id" in errs[0] and "at most 128 characters" in errs[0]
    assert "is not a model id" in overrides.validate_node_override_fields({"model": model})[0]
