"""How `kraft repo connect` sets up a pip project (a requirements.txt): a
virtualenv no work item commits, the dev requirements beside the base ones,
and a runner's or CI's test run inside it."""

from __future__ import annotations

import shlex
import subprocess

import pytest
from support.probe import chosen as _chosen
from support.probe import propose as _propose
from support.probe import repo_with as _repo

from kraft import detect

#: `python3 -m venv DIR` as Python 3.12 and older make one: no
#: `DIR/.gitignore`, which 3.13's `venv` writes and they do not. Its
#: `python` and `pytest` say they ran, in `DIR/ran`.
_PY312_VENV = """#!/bin/sh
[ "$1 $2" = "-m venv" ] || exit 2
mkdir -p "$3/bin" && echo "home = /usr/bin" > "$3/pyvenv.cfg"
for tool in pip python pytest; do
  printf '#!/bin/sh\\necho "$0 $*" >> "%s/ran"\\n' "$PWD/$3" > "$3/bin/$tool"
  chmod +x "$3/bin/$tool"
done
"""


def _py312(tmp_path) -> dict[str, str]:
    """An environment whose `python3` is `_PY312_VENV`, and with no `pytest`."""
    stub = tmp_path / "py312"
    stub.mkdir()
    (stub / "python3").write_text(_PY312_VENV)
    (stub / "python3").chmod(0o755)
    return {"PATH": f"{stub}:/usr/bin:/bin"}


@pytest.mark.parametrize("dev", ["requirements.txt", "requirements-dev.txt"])
def test_the_pip_setup_leaves_nothing_for_a_work_item_to_commit(tmp_path, dev):
    """The straggler sweep commits whatever git does not ignore: a `.venv/`
    the repo never ignored, made by a Python whose `venv` writes no
    `.gitignore`, rode into the merge request whole (1,023 files)."""
    repo = _repo(tmp_path, {dev: "pytest\n"})
    setup = _propose(repo).setup_command
    subprocess.run(["sh", "-c", setup], cwd=repo, env=_py312(tmp_path), check=True)
    assert (repo / ".venv" / "pyvenv.cfg").is_file()
    status = subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True)
    assert status.stdout.decode() == ""


@pytest.mark.parametrize("d", ["", "api"], ids=["root", "nested"])
@pytest.mark.parametrize(
    ("files", "test"),
    [
        ({"script/test": "#!/bin/sh\npytest -q\n"}, None),
        (
            {
                ".github/workflows/ci.yml": "on: push\njobs:\n  t:\n    steps:\n"
                "      - run: python -m unittest discover -s tests\n"
            },
            ".venv/bin/python -m unittest discover -s tests",
        ),
    ],
    ids=["a-runners-task", "a-ci-line"],
)
def test_a_test_paired_with_the_pip_setup_runs_in_its_venv(tmp_path, d, files, test):
    """A runner's `script/test` or CI's `python -m unittest` beside a
    requirements.txt got the pip setup's `.venv`, and ran outside it, with
    none of what it installed. Run as dispatch runs a test command: split
    into argv, no shell, from the worktree's root."""
    if d and ".github/workflows/ci.yml" in files:
        files = {
            ".github/workflows/ci.yml": files[".github/workflows/ci.yml"].replace(
                "      - run:", f"      - working-directory: {d}\n        run:"
            )
        }
    else:
        files = {f"{d}/{k}" if d else k: v for k, v in files.items()}
    reqs = f"{d}/requirements.txt" if d else "requirements.txt"
    repo = _repo(
        tmp_path,
        {**files, reqs: "humanize\n"},
        executable=tuple(k for k in files if k.endswith("script/test")),
    )
    p = _propose(repo)
    if test is not None:
        assert detect.in_dir(d, test, shell=False) == p.test_command
    env = _py312(tmp_path)
    subprocess.run(["sh", "-c", p.setup_command], cwd=repo, env=env, check=True)
    subprocess.run(shlex.split(p.test_command), cwd=repo, env={"PATH": "/usr/bin:/bin"}, check=True)
    ran = (repo / d / ".venv" / "ran").read_text()
    assert ("pytest -q" if test is None else "python -m unittest") in ran


_VENV = "python3 -m venv .venv && echo '*' > .venv/.gitignore && .venv/bin/pip install"


@pytest.mark.parametrize(
    ("files", "installs", "source"),
    [
        (
            {"requirements.txt": "humanize\n", "requirements-dev.txt": "pytest\n"},
            "-r requirements.txt -r requirements-dev.txt",
            "requirements.txt + requirements-dev.txt",
        ),
        (
            {
                "requirements.txt": "humanize\n",
                "dev-requirements.txt": "-r requirements.txt\npytest\n",
            },
            "-r dev-requirements.txt",
            "dev-requirements.txt",
        ),
        ({"requirements_dev.txt": "pytest\n"}, "-r requirements_dev.txt", "requirements_dev.txt"),
        ({"requirements.txt": "pytest\n"}, "-r requirements.txt", "requirements.txt"),
    ],
    ids=["beside-requirements", "including-requirements", "dev-alone", "requirements-alone"],
)
def test_a_dev_requirements_file_is_installed_with_the_one_it_does_not_include(
    tmp_path, files, installs, source
):
    """requirements-dev.txt alone was installed, without the requirements.txt
    beside it, and the setup was said to come from requirements.txt."""
    p = _propose(_repo(tmp_path, files))
    assert p.setup_command == f"{_VENV} {installs}"
    assert _chosen(p, "setup")["source"] == source
