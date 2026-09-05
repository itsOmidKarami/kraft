import os

import pytest

from kraft import steering


def _dir(tmp_path, files):
    d = tmp_path / "steering"
    d.mkdir()
    for name, body in files.items():
        (d / f"{name}.md").write_text(body)
    return d


def test_read_returns_bodies_in_the_order_named(tmp_path):
    d = _dir(tmp_path, {"a": "alpha", "b": "beta"})
    assert steering.read(d, ["b", "a"]) == ("beta", "alpha")


def test_no_names_never_touches_the_directory(tmp_path):
    """Keeps every existing fixture green: none of them ship a steering/ dir."""
    assert steering.read(tmp_path / "absent", []) == ()
    steering.validate(tmp_path / "absent", [], where="x")


def test_validate_names_the_missing_file_and_the_config_that_asked(tmp_path):
    d = _dir(tmp_path, {"a": "alpha"})
    with pytest.raises(steering.SteeringError, match="nope"):
        steering.validate(d, ["nope"], where="registry.yaml")


def test_validate_rejects_a_traversing_name(tmp_path):
    """`../../repo/CLAUDE` would read a file inside a target repo, which is the
    exact channel the context-injection boundary forbids."""
    d = _dir(tmp_path, {"a": "alpha"})
    for bad in ("../secret", "nested/thing", "/etc/passwd"):
        with pytest.raises(steering.SteeringError):
            steering.validate(d, [bad], where="x")


def test_validate_rejects_an_over_budget_total(tmp_path):
    d = _dir(tmp_path, {"big": "x" * steering.MAX_BYTES})
    with pytest.raises(steering.SteeringError, match="8192"):
        steering.validate(d, ["big"], where="x")


def test_the_budget_is_the_total_not_per_file(tmp_path):
    half = "x" * (steering.MAX_BYTES // 2 + 10)
    d = _dir(tmp_path, {"a": half, "b": half})
    with pytest.raises(steering.SteeringError):
        steering.validate(d, ["a", "b"], where="x")


@pytest.mark.skipif(os.getuid() == 0, reason="root ignores the mode bits")
def test_a_permissions_error_is_not_reported_as_missing(tmp_path):
    d = _dir(tmp_path, {"a": "alpha"})
    (d / "a.md").chmod(0o000)
    try:
        with pytest.raises(steering.SteeringError, match="cannot read"):
            steering.validate(d, ["a"], where="x")
    finally:
        (d / "a.md").chmod(0o644)


def test_validate_reports_invalid_utf8_as_a_steering_error_not_a_traceback(tmp_path):
    d = _dir(tmp_path, {"a": "alpha"})
    (d / "a.md").write_bytes(b"\xff\xfe\x00")
    with pytest.raises(steering.SteeringError, match="a"):
        steering.validate(d, ["a"], where="x")


def test_validate_accepts_a_total_exactly_at_the_budget(tmp_path):
    body = "x" * (steering.MAX_BYTES - steering._OVERHEAD)
    d = _dir(tmp_path, {"a": body})
    steering.validate(d, ["a"], where="x")


def test_read_reports_a_file_deleted_after_validation_as_a_steering_error(tmp_path):
    """Kraft-fza: validate runs at config load, read runs at dispatch, and the
    file can go away in between. The dispatch path catches SteeringError, so a
    bare FileNotFoundError would arrive as an unrelated crash."""
    d = _dir(tmp_path, {"a": "alpha"})
    (d / "a.md").unlink()
    with pytest.raises(steering.SteeringError, match="cannot read 'a'"):
        steering.read(d, ["a"])
