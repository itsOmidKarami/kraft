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
    ],
    ids=["home", "kraft-home", "inside-kraft-home", "inside-gh", "holds-home", "inside-home"],
)
def test_protected_refusal(host, tmp_path, rel, refused):
    home, kraft, _ = host
    (tmp_path / "home" / ".cargo" / "bin").mkdir(parents=True)
    got = host_view.protected_refusal((tmp_path / rel).resolve(), home, kraft)
    assert (got is not None) is refused


def _resolve(host, tmp_path, executable, path_dirs=(), workdir=None):
    home, kraft, checkout = host
    search = ":".join([*map(str, path_dirs), SYSTEM_PATH])
    return host_view.resolve(executable, search, workdir or checkout, checkout, home, kraft)


def _at(base: dict[str, Path], text: str) -> Path:
    """`~/x` in the fake home, `./x` in the checkout."""
    return base[text[0]] / text[2:]


@pytest.mark.parametrize(
    ("files", "executable", "path_dirs", "roots", "links", "argv0"),
    [
        (
            [
                ("~/.local/share/claude/versions/2.0.0", None),
                ("~/.local/bin/claude", "L:~/.local/share/claude/versions/2.0.0"),
            ],
            "claude",
            ["~/.local/bin"],
            ["~/.local/share/claude/versions"],
            [("~/.local/bin/claude", "~/.local/share/claude/versions/2.0.0")],
            "claude",
        ),
        (
            [
                ("~/.local/lib/rustup/rustup", None),
                ("~/.cargo/bin/cargo", "L:~/.local/lib/rustup/rustup"),
            ],
            "cargo",
            ["~/.cargo/bin"],
            ["~/.local/lib/rustup"],
            [("~/.cargo/bin/cargo", "~/.local/lib/rustup/rustup")],
            "cargo",
        ),
        ([("./gradlew", "#!/bin/sh\n")], "./gradlew", [], [], [], "./gradlew"),
        (
            [
                ("~/.local/share/uv/python/cpython-3.12/bin/python3.12", None),
                ("./.venv/bin/python", "L:~/.local/share/uv/python/cpython-3.12/bin/python3.12"),
            ],
            ".venv/bin/python",
            [],
            ["~/.local/share/uv/python/cpython-3.12"],
            [],
            ".venv/bin/python",
        ),
        ([("~/.local/bin/uv", None)], "uv", ["~/.local/bin"], ["~/.local/bin"], [], "uv"),
        (
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
            ["~/.nvm/node/lib/node_modules/@openai/codex", "~/.nvm/node"],
            None,
            "codex",
        ),
        ([("~/.cargo/bin/x", None)], "x", ["~/.cargo/bin"], ["~/.cargo"], [], "x"),
        ([], "sh", [], [], [], "sh"),
    ],
    ids=[
        "native-claude",
        "rustup-proxy",
        "in-checkout",
        "venv-python",
        "plain-file-local-bin",
        "node-cli",
        "cargo-install",
        "system",
    ],
)
def test_install_roots(host, files, executable, path_dirs, roots, links, argv0):
    home, kraft, checkout = host
    base = {"~": home, ".": checkout}
    for rel, what in files:
        path = _at(base, rel)
        if what and what.startswith("L:"):
            _link(path, _at(base, what[2:]))
        else:
            _file(path, what or "")
    got = host_view.resolve(
        executable,
        ":".join([*(str(_at(base, d + "/x").parent) for d in path_dirs), SYSTEM_PATH]),
        checkout,
        checkout,
        home,
        kraft,
    )
    assert got.argv0 == argv0
    assert set(got.roots) == {_at(base, r) for r in roots}
    if links is not None:
        assert got.links == tuple((_at(base, a), _at(base, b)) for a, b in links)


def test_install_roots_refused_and_missing(host, tmp_path):
    home, kraft, _ = host
    _file(home / "claude")
    with pytest.raises(Refused):
        _resolve(host, tmp_path, "claude", [home])
    _link(home / ".local/bin/sneaky", _file(kraft / "run/tool"))
    with pytest.raises(Refused):
        _resolve(host, tmp_path, "sneaky", [home / ".local/bin"])
    with pytest.raises(NotFound):
        _resolve(host, tmp_path, "no-such-tool-anywhere")


@pytest.mark.parametrize(
    ("rel", "found"),
    [("a/b/c/credentials.toml", True), ("a/b/c/d/credentials.toml", False), (".npmrc", True)],
    ids=["depth-3-found", "depth-4-ignored", "top-level"],
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


@pytest.mark.parametrize("system", [True, False], ids=["system-python", "kraft-python"])
def test_init_interpreter(monkeypatch, tmp_path, system):
    if not system:
        monkeypatch.setattr(host_view, "SYSTEM_PYTHON", tmp_path / "absent")
    exe, roots = host_view.init_interpreter()
    if system:
        assert (exe, roots) == (host_view.SYSTEM_PYTHON, ())
    else:
        assert exe == Path(os.path.realpath(sys.executable))
        assert roots == (Path(sys.base_prefix),)


@pytest.mark.parametrize(
    ("where", "kept"),
    [("ca.pem", True), ("cadir", True), ("missing.pem", False), (".ssh/ca.pem", False)],
    ids=["file", "dir", "missing-skipped", "protected-skipped"],
)
def test_ca_mounts(host, where, kept):
    home, kraft, _ = host
    (home / "ca.pem").write_text("x")
    (home / "cadir").mkdir()
    (home / ".ssh/ca.pem").write_text("x")
    got = host_view.ca_mounts({"SSL_CERT_FILE": str(home / where)}, home, kraft)
    assert got == ([(home / where).resolve()] if kept else [])
