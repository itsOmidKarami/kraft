from enum import StrEnum

import pytest

from kraft.vocab.total import total


class Color(StrEnum):
    RED = "red"
    BLUE = "blue"


def test_total_returns_a_read_only_mapping_in_declaration_order():
    rows = total(Color, {Color.BLUE: 2, Color.RED: 1}, name="ROWS")
    assert list(rows) == [Color.RED, Color.BLUE]
    with pytest.raises(TypeError):
        rows[Color.RED] = 3  # type: ignore[index]


@pytest.mark.parametrize(
    ("given", "message"),
    [
        ({Color.RED: 1}, r"Color\.BLUE has no row in ROWS"),
        ({Color.RED: 1, Color.BLUE: 2, "green": 3}, r"ROWS has a row for 'green'"),
    ],
    ids=["missing-member", "extra-key"],
)
def test_total_names_what_is_wrong(given, message):
    with pytest.raises(ValueError, match=message):
        total(Color, given, name="ROWS")
