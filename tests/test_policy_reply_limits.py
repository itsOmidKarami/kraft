"""The three reply limits (Part 2, item 3a1): code defaults, the administrator maximum,
where each key may be set, and a positive integer."""

import pytest
from pydantic import ValidationError

from kraft import policy

_KEYS = ("reply_cap_per_thread", "reply_rounds_per_head", "reply_timeout_minutes")


def _from(data: dict) -> policy.InstancePolicy:
    return policy.InstancePolicy.from_input(policy.InstancePolicyInput.model_validate(data))


@pytest.mark.parametrize(
    "data, expected",
    [
        pytest.param({}, (2, 2, 5), id="code-defaults"),
        pytest.param(
            {"defaults": {"reply_timeout_minutes": 9}}, (2, 2, 9), id="a-default-moves-it"
        ),
        pytest.param(
            {"maxima": {"reply_rounds_per_head": 1}}, (2, 1, 5), id="maximum-holds-the-code-default"
        ),
    ],
)
def test_the_reply_keys_have_code_defaults(data, expected):
    got = _from(data)

    assert (
        got.reply_cap_per_thread,
        got.reply_rounds_per_head,
        got.reply_timeout_minutes,
    ) == expected


@pytest.mark.parametrize("key", _KEYS, ids=["reply-cap", "reply-rounds", "reply-timeout"])
def test_a_reply_default_above_its_maximum_is_rejected(key):
    with pytest.raises(ValidationError, match=f"defaults.{key} 5 exceeds maxima.{key} 3"):
        policy.InstancePolicyInput.model_validate({"defaults": {key: 5}, "maxima": {key: 3}})


@pytest.mark.parametrize("key", _KEYS, ids=["reply-cap", "reply-rounds", "reply-timeout"])
def test_an_override_above_the_administrator_maximum_is_refused(key):
    pol = _from({"maxima": {key: 4}})

    assert pol.apply_template_override({key: 4}).__dict__[key] == 4
    with pytest.raises(policy.PolicyError, match=key):
        pol.apply_template_override({key: 5})


@pytest.mark.parametrize(
    "model, data, refused",
    [
        pytest.param(
            policy.TaskPolicyOverride, {"reply_cap_per_thread": 1}, True, id="task-refuses-cap"
        ),
        pytest.param(
            policy.TemplatePolicyOverride,
            {"reply_cap_per_thread": 1, "reply_rounds_per_head": 1},
            False,
            id="template-accepts-cap",
        ),
        pytest.param(
            policy.TaskPolicyOverride,
            {"reply_timeout_minutes": 1},
            False,
            id="task-accepts-timeout",
        ),
    ],
)
def test_a_node_only_key_is_refused_below_a_node(model, data, refused):
    if refused:
        with pytest.raises(ValidationError, match="reply_cap_per_thread"):
            model.model_validate(data)
    else:
        assert model.model_validate(data).model_dump(exclude_none=True) == data


@pytest.mark.parametrize(
    "value", [0, "none", -1, 2.5], ids=["zero", "none-string", "negative", "float"]
)
def test_a_reply_key_is_a_positive_integer(value):
    for key in _KEYS:
        for model in (
            policy.PolicyDefaultsInput,
            policy.PolicyMaximaInput,
            policy.TemplatePolicyOverride,
        ):
            with pytest.raises(ValidationError):
                model.model_validate({key: value})


def test_the_meet_takes_the_smallest_reply_value():
    meet = policy.TemplatePolicyOverride.meet(
        [
            policy.TemplatePolicyOverride(reply_cap_per_thread=4, reply_timeout_minutes=9),
            policy.TemplatePolicyOverride(reply_cap_per_thread=3, reply_rounds_per_head=2),
            policy.TemplatePolicyOverride(reply_timeout_minutes=6),
        ]
    )

    assert (meet.reply_cap_per_thread, meet.reply_rounds_per_head, meet.reply_timeout_minutes) == (
        3,
        2,
        6,
    )
