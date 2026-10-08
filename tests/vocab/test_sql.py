import pytest

from kraft.vocab.sql import in_list, marks


def test_in_list_is_one_line_by_default():
    assert in_list(["a", "b"]) == "'a', 'b'"


def test_in_list_wraps_greedily_like_the_hand_written_ddl():
    # start=2: the list begins at column 2; later lines are indented 4; width 14.
    assert in_list(["aa", "bb", "cc"], start=2, hang=4, width=14) == "'aa', 'bb',\n    'cc'"


@pytest.mark.parametrize("bad", ["a'b", "A", "a b", ""], ids=["quote", "upper", "space", "empty"])
def test_in_list_refuses_a_value_that_is_not_a_plain_identifier(bad):
    with pytest.raises(ValueError, match="not a plain"):
        in_list([bad])


def test_marks_counts_a_group():
    assert marks(("x", "y", "z")) == "?, ?, ?"
