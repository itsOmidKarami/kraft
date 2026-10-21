"""What a `kind: os` sandbox may see of the host: the protected-path rule, the
roots an executable needs, `expose:`, and the CA files that cross."""

import os
import sys
from pathlib import Path

import pytest

from kraft.worker import host_view
from kraft.worker.host_view import NotFound, Refused

SYSTEM_PATH = "/usr/bin:/bin"


@pytest.fixture
def host(tmp_path):
    """A fake operator home, a Kraft home inside it and a checkout beside it."""
    home = tmp_path / "home"
    kraft = home / ".kraft"
    checkout = tmp_path / "checkout"
    for d in (kraft / "run", home / ".config" / "gh", home / ".ssh", checkout):
        d.mkdir(parents=True)
    return home, kraft, checkout


def _file(path: Path, text: str = "", mode: int = 0o755) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(mode)
    return path


def _link(path: Path, target) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.symlink_to(target)
    return path


@pytest.mark.parametrize(
    ("rel", "refused"),
    [
        ("home", True),
        ("home/.kraft", True),
        ("home/.kraft/run", True),
        ("home/.config/gh/hosts.yml", True),
        (".", True),
        ("home/.cargo/bin", False),
        ("home/.netrc", True),
    ],
    ids=[
        "home",
        "kraft-home",
        "inside-kraft-home",
        "inside-gh",
        "holds-home",
        "inside-home",
        "credential-file",
    ],
)
def test_protected_refusal(host, tmp_path, rel, refused):
    home, kraft, _ = host
    (tmp_path / "home" / ".cargo" / "bin").mkdir(parents=True)
    (tmp_path / "home" / ".netrc").write_text("x")
    got = host_view.protected_refusal((tmp_path / rel).resolve(), home, kraft)
    assert (got is not None) is refused


def _at(base: dict[str, Path], text: str) -> Path:
    """`~/x` in the fake home, `./x` in the checkout."""
    return base[text[0]] / text[2:]


def _build(host, files):
    home, _, checkout = host
    base = {"~": home, ".": checkout}
    for rel, what in files:
        path = _at(base, rel)
        if what and what.startswith("L:"):
            _link(path, _at(base, what[2:]))
        else:
            _file(path, (what or "").replace("#!~", f"#!{home}"))
    return base


def _run(host, base, executable, path_dirs=(), visible=()):
    home, kraft, checkout = host
    search = ":".join([*(str(_at(base, d + "/x").parent) for d in path_dirs), SYSTEM_PATH])
    return host_view.resolve(
        executable, search, checkout, checkout, home, kraft, [_at(base, v) for v in visible]
    )


@pytest.mark.parametrize(
    ("files", "executable", "path_dirs", "visible", "roots", "links", "argv0"),
    [
        pytest.param(
            [
                ("~/.local/share/claude/versions/2.0.0", None),
                ("~/.local/bin/claude", "L:~/.local/share/claude/versions/2.0.0"),
            ],
            "claude",
            ["~/.local/bin"],
            [],
            ["~/.local/share/claude/versions"],
            [("~/.local/bin/claude", "~/.local/share/claude/versions/2.0.0")],
            "claude",
            id="native-claude",
        ),
        pytest.param(
            [
                ("~/.local/lib/rustup/rustup", None),
                ("~/.cargo/bin/cargo", "L:~/.local/lib/rustup/rustup"),
            ],
            "cargo",
            ["~/.cargo/bin"],
            [],
            ["~/.local/lib/rustup"],
            [("~/.cargo/bin/cargo", "~/.local/lib/rustup/rustup")],
            "cargo",
            id="rustup-proxy",
        ),
        pytest.param(
            [("./gradlew", "#!/bin/sh\n")],
            "./gradlew",
            [],
            [],
            [],
            [],
            "./gradlew",
            id="in-checkout",
        ),
        pytest.param(
            [
                ("~/.local/share/uv/python/cpython-3.12/bin/python3.12", None),
                ("./.venv/bin/python", "L:~/.local/share/uv/python/cpython-3.12/bin/python3.12"),
            ],
            ".venv/bin/python",
            [],
            ["~/.local/share/uv/python"],
            [],
            [],
            ".venv/bin/python",
            id="venv-python",
        ),
        pytest.param(
            [
                ("~/.local/share/uv/python/cpython-3.12.13/bin/python3.12", None),
                (
                    "~/.local/share/uv/python/cpython-3.12",
                    "L:~/.local/share/uv/python/cpython-3.12.13",
                ),
                ("./.venv/bin/python", "L:~/.local/share/uv/python/cpython-3.12/bin/python3.12"),
            ],
            ".venv/bin/python",
            [],
            ["~/.local/share/uv/python/cpython-3.12.13"],
            [],
            [("~/.local/share/uv/python/cpython-3.12", "~/.local/share/uv/python/cpython-3.12.13")],
            ".venv/bin/python",
            id="venv-uv-minor-link",
        ),
        pytest.param(
            [("~/.local/bin/uv", None)],
            "uv",
            ["~/.local/bin"],
            [],
            ["~/.local/bin"],
            [],
            "uv",
            id="plain-file-local-bin",
        ),
        pytest.param(
            [
                (
                    "~/.nvm/node/lib/node_modules/@openai/codex/bin/codex.js",
                    "#!/usr/bin/env node\n",
                ),
                ("~/.nvm/node/bin/node", None),
                (
                    "~/.nvm/node/bin/codex",
                    "L:~/.nvm/node/lib/node_modules/@openai/codex/bin/codex.js",
                ),
            ],
            "codex",
            ["~/.nvm/node/bin"],
            [],
            ["~/.nvm/node/lib/node_modules/@openai/codex", "~/.nvm/node"],
            None,
            "codex",
            id="node-cli",
        ),
        pytest.param(
            [("~/.cargo/bin/x", None)],
            "x",
            ["~/.cargo/bin"],
            [],
            ["~/.cargo"],
            [],
            "x",
            id="cargo-install",
        ),
        pytest.param(
            [("~/.cargo/bin/cargo", None), ("~/.cargo/credentials", "token")],
            "cargo",
            ["~/.cargo/bin"],
            ["~/.cargo/bin"],
            [],
            [],
            "cargo",
            id="exposed-bin-not-widened",
        ),
        pytest.param([], "sh", [], [], [], [], "sh", id="system"),
        pytest.param(
            [("./run.sh", "#!~/.mozilla/ff/x\n")],
            "./run.sh",
            [],
            [],
            [],
            [],
            "./run.sh",
            id="checkout-script-shebang",
        ),
        pytest.param(
            [("~/realbin/tool", None), ("~/pbin", "L:~/realbin")],
            "tool",
            ["~/pbin"],
            [],
            ["~/realbin"],
            [("~/pbin", "~/realbin")],
            "tool",
            id="path-dir-link",
        ),
        pytest.param(
            [
                ("~/Documents/secret/bin/tool", None),
                ("./.venv/bin/tool", "L:~/Documents/secret/bin/tool"),
            ],
            ".venv/bin/tool",
            [],
            ["~/.cargo"],
            Refused,
            None,
            ".venv/bin/tool",
            id="checkout-link-outside-refused",
        ),
        pytest.param(
            [("~/tc/bin/tool", None), ("./.venv/bin/tool", "L:~/tc/bin/tool")],
            ".venv/bin/tool",
            [],
            ["~/tc"],
            [],
            [],
            ".venv/bin/tool",
            id="checkout-link-into-expose-ok",
        ),
        pytest.param(
            [("~/claude", None)], "claude", ["~"], [], Refused, None, "claude", id="at-home"
        ),
        pytest.param(
            [("~/.kraft/run/tool", None), ("~/.local/bin/sneaky", "L:~/.kraft/run/tool")],
            "sneaky",
            ["~/.local/bin"],
            [],
            Refused,
            None,
            "sneaky",
            id="via-kraft-home",
        ),
        pytest.param([], "no-such-tool-anywhere", [], [], NotFound, None, "x", id="missing"),
        pytest.param([], "./gradlew", [], [], NotFound, None, "x", id="path-named-missing"),
        pytest.param(
            [("~/tc/bin/tool", "#!/no/such/interp\n")],
            "tool",
            ["~/tc/bin"],
            [],
            NotFound,
            None,
            "tool",
            id="shebang-interpreter-missing",
        ),
        pytest.param(
            [("~/tc/bin/tool", "#!/usr/bin/env FOO=1 sh\n")],
            "tool",
            ["~/tc/bin"],
            [],
            ["~/tc"],
            None,
            "tool",
            id="env-assignment-skipped",
        ),
        pytest.param(
            [("~/tc/bin/tool", "#!/usr/bin/env no-such-interp-xyz\n")],
            "tool",
            ["~/tc/bin"],
            [],
            NotFound,
            None,
            "tool",
            id="env-target-missing",
        ),
        pytest.param(
            [("~/dotlocal/bin/uv", None), ("~/.local", "L:~/dotlocal")],
            "uv",
            ["~/.local/bin"],
            [],
            ["~/dotlocal/bin"],
            None,
            "uv",
            id="local-is-symlink",
        ),
    ],
)
def test_install_roots(host, files, executable, path_dirs, visible, roots, links, argv0):
    base = _build(host, files)
    if isinstance(roots, type):
        with pytest.raises(roots) as raised:
            _run(host, base, executable, path_dirs, visible)
        # A miss on PATH is fixed on the server's PATH: `expose` never joins it.
        why = str(raised.value)
        assert roots is not NotFound or "server's PATH" in why or "does not exist" in why
        return
    got = _run(host, base, executable, path_dirs, visible)
    assert got.argv0 == argv0
    assert set(got.roots) == {_at(base, r) for r in roots}
    if links is not None:
        assert got.links == tuple((_at(base, a), _at(base, b)) for a, b in links)


@pytest.mark.parametrize(
    ("rel", "found"),
    [
        ("a/b/c/credentials.toml", True),
        ("a/b/c/d/credentials.toml", False),
        (".npmrc", True),
        ("credentials", True),
    ],
    ids=["depth-3-found", "depth-4-ignored", "top-level", "cargo-legacy"],
)
def test_credential_shadows(tmp_path, rel, found):
    _file(tmp_path / rel, "token")
    assert (host_view.credential_shadows(tmp_path) == [tmp_path / rel]) is found


@pytest.mark.parametrize(
    ("entry", "error"),
    [("~/.cargo/bin", None), ("~/.ssh", Refused), ("~/.gone", NotFound)],
    ids=["tilde-expanded", "protected", "missing"],
)
def test_expose_roots(host, entry, error):
    home, kraft, _ = host
    (home / ".cargo/bin").mkdir(parents=True)
    if error:
        with pytest.raises(error):
            host_view.expose_roots([entry], home, kraft)
    else:
        assert host_view.expose_roots([entry], home, kraft) == [home / ".cargo/bin"]


@pytest.fixture
def fresh_interpreter():
    host_view.init_interpreter.cache_clear()
    yield
    host_view.init_interpreter.cache_clear()


@pytest.mark.parametrize("system", [True, False], ids=["system-python", "kraft-python"])
def test_init_interpreter(fresh_interpreter, monkeypatch, tmp_path, system):
    if system and host_view.init_interpreter()[0] != host_view.SYSTEM_PYTHON:
        pytest.skip("no /usr/bin/python3 of 3.10 or later on this host")
    if not system:
        monkeypatch.setattr(host_view, "SYSTEM_PYTHON", tmp_path / "absent")
    exe, roots = host_view.init_interpreter()
    if system:
        assert (exe, roots) == (host_view.SYSTEM_PYTHON, ())
    else:
        assert exe == Path(os.path.realpath(sys.executable))
        assert roots == (Path(sys.base_prefix).resolve(),)


@pytest.mark.parametrize(
    ("where", "kept"),
    [
        ("ca.pem", True),
        ("cadir", True),
        ("missing.pem", False),
        (".ssh/ca.pem", False),
        ("link.pem", True),
        ("unreadable.pem", False),
    ],
    ids=[
        "file",
        "dir",
        "missing-skipped",
        "protected-skipped",
        "symlink-as-written",
        "unreadable-skipped",
    ],
)
def test_ca_mounts(host, where, kept, monkeypatch):
    home, kraft, _ = host
    real_exists = Path.exists

    def exists(self, *a, **kw):  # a parent the user cannot search, as root cannot be made to
        if self.name == "unreadable.pem":
            raise PermissionError(13, "Permission denied", str(self))
        return real_exists(self, *a, **kw)

    monkeypatch.setattr(Path, "exists", exists)
    (home / "ca.pem").write_text("x")
    (home / "cadir").mkdir()
    (home / ".ssh/ca.pem").write_text("x")
    (home / "link.pem").symlink_to(home / "ca.pem")
    got = host_view.ca_mounts({"SSL_CERT_FILE": str(home / where)}, home, kraft)
    assert got == ([home / where] if kept else [])


@pytest.mark.parametrize(
    ("files", "executable", "error"),
    [
        pytest.param(
            [("~/Documents/secret/bin/tool", None), ("./evil", "L:~/Documents/secret/bin/tool")],
            "./evil",
            Refused,
            id="checkout-via-symlink-refused",
        ),
        pytest.param(
            [("./script", "#!~/.mozilla/ff/x\n"), ("~/.local/bin/tool", "L:./script")],
            "tool",
            None,
            id="final-in-checkout-no-shebang",
        ),
    ],
)
def test_checkout_by_any_spelling(host, tmp_path, files, executable, error):
    """The checkout is a symlinked directory; a path reaching it through the
    link, or a link leading into it, is still the repo's own."""
    home, kraft, checkout = host
    alias = tmp_path / "alias"
    alias.symlink_to(checkout)
    _build(host, files)
    search = f"{home / '.local/bin'}:{SYSTEM_PATH}"
    args = (executable, search, alias, checkout, home, kraft)
    if error:
        with pytest.raises(error):
            host_view.resolve(*args)
    else:
        assert host_view.resolve(*args).roots == ()
