"""`policy.yaml`'s slice caps: `max_slices` and `max_concurrent_slices`."""

import pytest
from test_policy import _BASE

from kraft import policy


@pytest.mark.parametrize(
    "doc",
    [
        pytest.param(_BASE + "max_slices: 0\n", id="max-slices-zero"),
        pytest.param(_BASE + "max_concurrent_slices: 0\n", id="max-concurrent-slices-zero"),
    ],
)
def test_load_policy_rejects_a_slice_cap_below_1(tmp_path, doc):
    d = tmp_path / "policy.yaml"
    d.write_text(doc)
    with pytest.raises(policy.PolicyError):
        policy.load_policy(d)


@pytest.mark.parametrize(
    ("tail", "attr", "expected"),
    [
        pytest.param("max_slices: 4\n", "max_slices", 4, id="max-slices"),
        pytest.param("", "max_slices", 6, id="max-slices-default"),
        pytest.param(
            "max_concurrent_slices: 2\n", "max_concurrent_slices", 2, id="max-concurrent-slices"
        ),
        pytest.param("", "max_concurrent_slices", None, id="max-concurrent-slices-default"),
    ],
)
def test_load_policy_reads_a_slice_cap(tmp_path, tail, attr, expected):
    d = tmp_path / "policy.yaml"
    d.write_text(_BASE + tail)
    assert getattr(policy.load_policy(d), attr) == expected
