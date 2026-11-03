"""A repository id is never `kraft-default`, the built-in add-on's namespace."""

import pytest
from support.harness import make_repo_with_submodule

from kraft.addons import builtin
from kraft.config import read_yaml, write_yaml

pytestmark = pytest.mark.api_client(default_setup=False)


def test_a_minted_repo_id_steps_past_the_builtin(tmp_path, client):
    root, _sub = make_repo_with_submodule(tmp_path, submodule_path=f"libs/{builtin.NAMESPACE}")
    client.post("/api/repos", json={"path": str(root), "enabled": False})
    ids = {r["path"]: r.get("id") for r in client.get("/api/repos").json()["repos"]}
    assert ids[str(root / "libs" / builtin.NAMESPACE)] == f"{builtin.NAMESPACE}-2"


def test_an_explicit_repo_id_of_the_builtin_is_refused(tmp_path, client, templates_dir):
    root, member = make_repo_with_submodule(tmp_path, submodule_path="libs/a")
    client.post("/api/repos", json={"path": str(member), "test_command": "make test"})
    written = read_yaml(templates_dir / "repos.yaml")
    written["repos"][0]["id"] = builtin.NAMESPACE
    write_yaml(templates_dir / "repos.yaml", written)
    before = (templates_dir / "repos.yaml").read_text()

    answer = client.post("/api/repos", json={"path": str(root), "enabled": False})

    assert answer.status_code == 422
    assert (templates_dir / "repos.yaml").read_text() == before
