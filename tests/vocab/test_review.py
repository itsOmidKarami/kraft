import pytest

from kraft.vocab import DiffSide, ReplyClaim, ReviewOutcome, ThreadLabel, ThreadState


@pytest.mark.parametrize(
    ("enum", "values"),
    [
        pytest.param(ReviewOutcome, ["approve", "request_changes", "comment"], id="outcome"),
        pytest.param(ThreadLabel, ["must_fix", "question", "nit"], id="label"),
        pytest.param(ThreadState, ["open", "claimed", "resolved"], id="state"),
        pytest.param(ReplyClaim, ["fixed", "answered", "should_fix"], id="claim"),
        pytest.param(DiffSide, ["old", "new"], id="side"),
    ],
)
def test_the_review_sets_are_todays_values(enum, values):
    assert [m.value for m in enum] == values
