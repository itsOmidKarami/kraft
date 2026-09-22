import os

import pytest

from kraft.worker import steering


def _dir(tmp_path, files):
    d = tmp_path / "steering"
    d.mkdir()
    for name, body in files.items():
        (d / f"{name}.md").write_text(body)
    return d


def test_read_returns_bodies_in_the_order_named(tmp_path):
    d = _dir(tmp_path, {"a": "alpha", "b": "beta"})
    assert steering.Steering(dir=d).read(["b", "a"]) == ("beta", "alpha")


def test_no_names_never_touches_the_directory(tmp_path):
    """Keeps every existing fixture green: none of them ship a steering/ dir."""
    assert steering.Steering(dir=tmp_path / "absent").read([]) == ()
    steering.Steering(dir=tmp_path / "absent").validate([], where="x")


_HALF = "x" * (steering.Steering.MAX_BYTES // 2 + 10)


@pytest.mark.parametrize(
    "names, match",
    [
        # Names the missing file (and so the config that asked for it).
        (["nope"], "nope"),
        # `../../repo/CLAUDE` would read a file inside a target repo, the exact
        # channel the context-injection boundary forbids.
        (["../secret"], "bare file name"),
        (["nested/thing"], "bare file name"),
        (["/etc/passwd"], "bare file name"),
        (["big"], "8192"),
        # The budget is the total, not per file.
        (["half1", "half2"], "8192"),
        # Invalid UTF-8 is a SteeringError, not a traceback.
        (["binary"], "binary"),
    ],
    ids=[
        "missing-file",
        "parent-traversal",
        "nested-path",
        "absolute-path",
        "one-file-over-budget",
        "total-over-budget",
        "invalid-utf8",
    ],
)
def test_validate_rejects(tmp_path, names, match):
    d = _dir(
        tmp_path,
        {"a": "alpha", "big": "x" * steering.Steering.MAX_BYTES, "half1": _HALF, "half2": _HALF},
    )
    (d / "binary.md").write_bytes(b"\xff\xfe\x00")
    with pytest.raises(steering.SteeringError, match=match):
        steering.Steering(dir=d).validate(names, where="registry.yaml")


@pytest.mark.skipif(os.getuid() == 0, reason="root ignores the mode bits")
def test_a_permissions_error_is_not_reported_as_missing(tmp_path):
    d = _dir(tmp_path, {"a": "alpha"})
    (d / "a.md").chmod(0o000)
    try:
        with pytest.raises(steering.SteeringError, match="cannot read"):
            steering.Steering(dir=d).validate(["a"], where="x")
    finally:
        (d / "a.md").chmod(0o644)


def test_validate_accepts_a_total_exactly_at_the_budget(tmp_path):
    body = "x" * (steering.Steering.MAX_BYTES - len(steering.Steering.HEADING.encode()))
    d = _dir(tmp_path, {"a": body})
    steering.Steering(dir=d).validate(["a"], where="x")
    # validate() returns nothing on success; read() is what proves the file
    # `validate` just accepted is actually the one at the boundary.
    assert steering.Steering(dir=d).read(["a"]) == (body,)


def test_read_reports_a_file_deleted_after_validation_as_a_steering_error(tmp_path):
    """Kraft-fza: validate runs at config load, read runs at dispatch, and the
    file can go away in between. The dispatch path catches SteeringError, so a
    bare FileNotFoundError would arrive as an unrelated crash."""
    d = _dir(tmp_path, {"a": "alpha"})
    (d / "a.md").unlink()
    with pytest.raises(steering.SteeringError, match="cannot read 'a'"):
        steering.Steering(dir=d).read(["a"])


def test_the_budget_measures_the_block_a_launch_injects(tmp_path):
    """Kraft-5d510.3: `Steering.block` is both what a launch's context carries
    and what `check_budget` measures, separators included, so the two cannot
    drift. Two bodies filling the budget exactly pass; one byte more fails."""
    fixed = len(steering.Steering.block(["", ""]).encode())
    half = (steering.Steering.MAX_BYTES - fixed) // 2
    bodies = ["x" * half, "y" * (steering.Steering.MAX_BYTES - fixed - half)]
    assert len(steering.Steering.block(bodies).encode()) == steering.Steering.MAX_BYTES
    steering.Steering.check_budget(bodies, where="x")
    with pytest.raises(steering.SteeringError, match="x: steering totals 8193 bytes"):
        steering.Steering.check_budget([*bodies[:1], bodies[1] + "z"], where="x")
