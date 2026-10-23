"""The machine keeps the server's files at paths of its own."""

from kraft.remote.paths import PathMap

MAP = PathMap((("/srv/run", "/m/run"), ("/srv/run/items/w1/wt", "/m/wt/w1")))


def test_a_server_path_becomes_the_machines_wherever_it_appears_and_the_longest_prefix_wins():
    text = "cat /srv/run/items/w1/docs/spec.md >> /srv/run/items/w1/wt/NOTES"
    assert MAP.text(text) == "cat /m/run/items/w1/docs/spec.md >> /m/wt/w1/NOTES"


def test_a_longer_name_that_merely_starts_with_a_prefix_is_left_alone():
    assert MAP.text("/srv/runner/x /srv/run") == "/srv/runner/x /m/run"


def test_covers_says_whether_a_path_is_under_a_mapped_prefix():
    assert MAP.covers("/srv/run/items/w1/docs") and not MAP.covers("/etc/passwd")
