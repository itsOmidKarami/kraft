"""What doctor says about the built-in `kraft-default`: its own row, and which
of its chains a home is judged on."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import yaml

from kraft import doctor, update
from kraft.addons import builtin
from kraft.api import config_check


def _codex_only(tmp_path, monkeypatch, local_default: dict | None):
    live = tmp_path / "templates"
    (live / "chains").mkdir(parents=True)
    (live / "library.yaml").write_text(
        yaml.safe_dump({"tasks": {"t": {"kind": "agent", "harness": "b", "prompt": "p"}}})
    )
    node = {"id": "n", "kind": "exec", "tasks": [{"id": "t", "extends": "t"}]}
    (live / "chains" / "mine.yaml").write_text(yaml.safe_dump({"id": "mine", "nodes": [node]}))
    if local_default is not None:
        (live / "chains" / "default.yaml").write_text(yaml.safe_dump(local_default))
    (live / "harnesses.yaml").write_text(
        yaml.safe_dump({"harnesses": {"b": {"provider": "codex"}}})
    )
    monkeypatch.setenv("KRAFT_CONFIG_DIR", str(live))


@pytest.mark.parametrize(
    ("local_default", "checked"),
    [
        (None, False),
        ({"id": "default", "nodes": [{"id": "n", "kind": "exec", "tasks": []}]}, False),
        ({"id": "default", "extends": "kraft-default:default"}, True),
    ],
    ids=["no-local-default", "default-that-does-not-extend", "default-that-extends"],
)
def test_a_codex_only_home_is_not_judged_on_chains_it_never_runs(
    tmp_path, monkeypatch, local_default, checked
):
    _codex_only(tmp_path, monkeypatch, local_default)
    names = {r["name"] for r in doctor._agent_checks()}
    assert ("agent: claude" in names) is checked


def test_a_repo_naming_a_builtin_chain_as_its_default_has_it_checked(tmp_path, monkeypatch):
    _codex_only(tmp_path, monkeypatch, None)
    live = tmp_path / "templates"
    entry = {"id": "r", "path": "/x", "default_chain": "kraft-default:default"}
    (live / "repos.yaml").write_text(yaml.safe_dump({"repos": [entry]}))
    assert "agent: claude" in {r["name"] for r in doctor._agent_checks()}


@pytest.mark.parametrize(
    ("named", "thin", "judged"),
    [(False, False, False), (True, False, True), (False, True, False), (True, True, True)],
    ids=["unnamed", "named-by-a-repo", "thin-local-default", "thin-and-named"],
)
def test_lint_selections_follow_the_repos_default_chain(tmp_path, monkeypatch, named, thin, judged):
    from kraft.addons import load
    from kraft.api import config_check
    from kraft.templates.library import TemplateLibrary

    local = {"id": "default", "extends": "kraft-default:default"} if thin else None
    _codex_only(tmp_path, monkeypatch, local)
    live = tmp_path / "templates"
    if named:
        entry = {"id": "r", "path": "/x", "default_chain": "kraft-default:default"}
        (live / "repos.yaml").write_text(yaml.safe_dump({"repos": [entry]}))
    library = TemplateLibrary.from_yaml_dir(live, addons=load.installed(live))
    chains = {c for c, _, _ in config_check.selections(library, live)}
    assert ("kraft-default:default" in chains) is judged


def _home(tmp_path, monkeypatch, *, chains=None, harnesses=None, repos=None, policy=None):
    """A config dir with the files given and a run dir beside it."""
    live = tmp_path / "templates"
    (live / "chains").mkdir(parents=True)
    for name, body in (chains or {}).items():
        (live / "chains" / f"{name}.yaml").write_text(yaml.safe_dump(body))
    packaged = builtin.source_dir() / "harnesses.yaml"
    (live / "harnesses.yaml").write_text(
        yaml.safe_dump(harnesses) if harnesses is not None else packaged.read_text()
    )
    if repos is not None:
        (live / "repos.yaml").write_text(yaml.safe_dump(repos))
    (live / "policy.yaml").write_text(yaml.safe_dump(_policy(policy or {})))
    monkeypatch.setenv("KRAFT_CONFIG_DIR", str(live))
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    return live


def _policy(over: dict) -> dict:
    """The packaged policy.yaml with `over` merged in: a bare mapping is not valid."""
    return {**yaml.safe_load((builtin.source_dir() / "policy.yaml").read_text()), **over}


_THIN = {"id": "default", "extends": "kraft-default:default"}
_FUTURE, _PAST = "99.0.0", "0.0.1"


def _recorded(status, to, lines=("chains/default.yaml: node gone",)):
    def record(tmp_path):
        builtin.record(
            tmp_path / "run" / "addons",
            status=status,
            from_="2.5.0",
            to=update.installed() if to == "installed" else to,
            lines=lines,
        )

    return record


_COPY = "chains/default.yaml is a full copy and hides kraft-default:default"
_HELD = "Run `kraft admin update` to see what it changes and decide"
_OVERRIDE = "after accepting, fix the override it names"


@pytest.mark.parametrize(
    ("setup", "says"),
    [
        (
            {"record": _recorded("held", _FUTURE)},
            ["the 99.0.0 update was held: chains/default.yaml: node gone. " + _HELD, _OVERRIDE],
        ),
        ({"record": _recorded("held", _PAST)}, []),
        (
            {"record": _recorded("held", _FUTURE, ("cannot compare: offline",))},
            ["the 99.0.0 update was held: cannot compare: offline. " + _HELD, "!" + _OVERRIDE],
        ),
        ({"record": _recorded("changed", "installed")}, ["changed kraft-default:", "node gone"]),
        ({"record": _recorded("changed", _FUTURE)}, []),
        (
            {
                "raw": '{"status": "held", "to": "99.0.0", "lines": 5}',
                "files": {"default": "nodes: []\n"},
            },
            ["cannot read kraft-default: its update record is malformed", _COPY],
        ),
        ({"raw": "[1]"}, []),
        (
            {"chains": {"default": {"id": "default", "nodes": []}}},
            [_COPY, "Move onto kraft-default"],
        ),
        ({"files": {"default": "nodes: []\n"}}, [_COPY]),
        ({"files": {"mine": "id: default\nnodes: []\n"}}, ["chains/mine.yaml is a full copy"]),
        ({"chains": {"default": _THIN}}, []),
        ({"stub": True}, ["kraft-default@acme: Kraft now ships kraft-default itself"]),
        (
            {"harnesses": {"harnesses": {"codex": {"provider": "codex"}}}},
            ["harnesses.yaml lacks claude, strong, fast; using the packaged ones"],
        ),
        (
            {"repos": {"repos": [{"id": "kraft-default", "path": "/x"}]}},
            ["repository id kraft-default is kraft-default's namespace; rename it"],
        ),
        (
            {"policy": {"maxima": {"tasks": {"time_cap_minutes": 1}}}},
            ["maxima: clamps kraft-default task(s): "],
        ),
        (
            {
                "record": _recorded("held", _FUTURE),
                "files": {"default": "nodes: []\n"},
                "stub": True,
                "harnesses": {"harnesses": {}},
                "repos": {"repos": [{"id": "kraft-default", "path": "/x"}]},
                "policy": {"maxima": {"tasks": {"time_cap_minutes": 1}}},
            },
            [
                "update was held",
                _COPY,
                "kraft-default@acme",
                "harnesses.yaml lacks",
                "repository id",
                "maxima:",
            ],
        ),
        ({}, []),
    ],
    ids=[
        "held",
        "held-then-installed",
        "held-cannot-compare",
        "changed",
        "changed-then-rolled-back",
        "malformed-record",
        "non-dict-record",
        "full-copy",
        "full-copy-without-an-id",
        "full-copy-under-another-name",
        "thin-is-quiet",
        "github-copy",
        "fallback",
        "repo-id",
        "maxima",
        "all-lines-in-order",
        "nothing-to-say",
    ],
)
def test_the_kraft_default_row(tmp_path, monkeypatch, setup, says):
    keys = ("chains", "harnesses", "repos", "policy")
    live = _home(tmp_path, monkeypatch, **{k: v for k, v in setup.items() if k in keys})
    for name, text in setup.get("files", {}).items():
        (live / "chains" / f"{name}.yaml").write_text(text)
    if "record" in setup:
        setup["record"](tmp_path)
    if "raw" in setup:
        (tmp_path / "run" / "addons").mkdir(parents=True)
        (tmp_path / "run" / "addons" / "builtin.json").write_text(setup["raw"])
    if setup.get("stub"):
        old = SimpleNamespace(
            id="kraft-default@acme", left_out=builtin.SHIPS_IT.format(id="kraft-default@acme")
        )
        real = doctor.addons_load.installed
        monkeypatch.setattr(doctor.addons_load, "installed", lambda *a, **k: [*real(*a, **k), old])

    row = doctor._builtin_check(live)

    assert row["name"] == "kraft-default" and row["ok"] is True
    present = [t for t in says if not t.startswith("!")]
    assert row["warn"] is bool(present), row
    for text in present:
        assert text in row["detail"], row
    for text in (t[1:] for t in says if t.startswith("!")):
        assert text not in row["detail"], row
    # Lines come in the plan's order.
    at = [row["detail"].index(t) for t in present]
    assert at == sorted(at), row


def test_lowering_maxima_clamps_the_builtin(client):
    st = client.app.state
    policy = _policy({"maxima": {"tasks": {"time_cap_minutes": 1}}})

    issues = config_check.check("policy.yaml", yaml.safe_dump(policy), config_check.context(st))

    messages = [i.message for i in issues]
    assert any("clamps kraft-default" in m for m in messages), messages
    assert not any("would leave add-on kraft-default" in m for m in messages), messages
