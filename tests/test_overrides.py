"""An operator's per-item override is held to one shape per kind: a work
item's own model/effort (`agent_overrides`) and a node's (`node_overrides`)."""

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
