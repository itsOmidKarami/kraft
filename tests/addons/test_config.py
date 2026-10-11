"""`config/addons.yaml` and `config/addons.lock`: what each refuses, and why."""

import pytest
import yaml

from kraft.addons.config import AddonsConfig, AddonsLock, RepoLibrariesLock
from kraft.config import ConfigError, write_yaml

URL = "https://github.com/acme/kraft-addons.git"
ACME = {"acme": {"git": URL}}
LOCAL = {"local": {"path": "/home/omid/src/my-addons"}}
OID = "3f2a9c" + "0" * 34


def _load(tmp_path, text):
    path = tmp_path / "addons.yaml"
    path.write_text(text if isinstance(text, str) else yaml.safe_dump(text))
    return AddonsConfig.load(path)


@pytest.mark.parametrize(
    ("data", "why"),
    [
        ({"collections": ACME, "extra": 1}, "extra"),
        ({"collections": ACME, "addons": {"release@acme": {"as_": "rel"}}}, "as_"),
        ({"collections": {"acme": {"git": URL, "path": "/x"}}}, "exactly one of git or path"),
        ({"collections": {"acme": {}}}, "exactly one of git or path"),
        ({"collections": {"acme": {"git": "acme/kraft-addons"}}}, "git must be a full URL"),
        ({"collections": {"acme": {"git": "http://github.com/a/b.git"}}}, "git must be a full URL"),
        (
            {"collections": {"acme": {"git": "https://u:tok@github.com/a/b.git"}}},
            "git must be a full URL",
        ),
        ({"collections": {"acme": {"git": "-u@host:repo"}}}, "git must be a full URL"),
        ({"collections": {"local": {"path": "addons"}}}, "path must be an absolute directory"),
        ({"collections": {"local": {"path": "/a/../b"}}}, "path must be an absolute directory"),
        ({"collections": {"acme": {"git": URL, "ref": "--upload-pack=x"}}}, "starts with '-'"),
        ("collections:\n  acme:\n    git: " + URL + "\n    ref: 1.10\n", "quote it"),
        ({"collections": ACME, "addons": {"release@other": True}}, "no collection 'other'"),
        (
            {"collections": ACME, "addons": {"release@acme": True, "x@acme": {"as": "release"}}},
            "namespace 'release' is taken",
        ),
        ({"collections": ACME, "addons": {"x@acme": {"as": "kraft"}}}, "Kraft's own namespace"),
        (
            {
                "collections": {"acme": {"git": URL, "auto_update": True}},
                "addons": {"release@acme": {"auto_update": False}},
            },
            "auto-updates every add-on",
        ),
        ({"collections": {"local": {**LOCAL["local"], "auto_update": True}}}, "never auto-updates"),
        (
            {"collections": LOCAL, "addons": {"tools@local": {"auto_update": True}}},
            "a directory collection never auto-updates",
        ),
    ],
    ids=[
        "unknown-key",
        "as-by-field-name",
        "git-and-path",
        "neither",
        "owner-repo",
        "http",
        "credentials",
        "leading-dash",
        "relative-path",
        "path-not-normal",
        "ref-option",
        "ref-number",
        "missing-collection",
        "duplicate-namespace",
        "kraft-alias",
        "opt-out-under-auto-collection",
        "directory-auto-update",
        "directory-addon-auto-update",
    ],
)
def test_addons_yaml_is_refused(tmp_path, data, why):
    with pytest.raises(ConfigError, match=why):
        _load(tmp_path, data)


@pytest.mark.parametrize(
    "data",
    [
        {"collections": ACME, "addons": {"release@acme": True, "legacy@acme": False}},
        "collections:\naddons:\n",
        {"collections": {"acme": {"git": URL, "ref": None}}},
        {"collections": {"acme": {"git": "ssh://git@host.example:2222/acme/x.git"}}},
        {"collections": {"acme": {"git": "git@github.com:acme/x.git"}}},
        {"collections": {"acme": {"git": URL, "ref": OID}}},
    ],
    ids=["short-forms", "empty-sections", "explicit-nulls", "ssh-port", "scp-like", "ref-commit"],
)
def test_addons_yaml_accepts(tmp_path, data):
    loaded = _load(tmp_path, data)
    for addon_id in loaded.addons:
        assert loaded.namespace(addon_id) == addon_id.split("@")[0]


LOCKED = {
    "namespace": "release",
    "ref": "main",
    "commit": OID,
    "source": "./addons/release",
    "tree": OID,
    "digest": "sha256:" + "7b" * 32,
    "version": "1.4.0",
    "updated_at": "2026-10-08T12:00:00Z",
}


@pytest.mark.parametrize(
    ("change", "why"),
    [
        ({"tree": None}, "commit and tree"),
        ({"source": "./../x"}, "source"),
        ({"digest": "md5:abc"}, "digest"),
        ({"version": "1.4"}, "version"),
        ({"updated_at": "2026-10-08T12:00:00"}, "updated_at"),
        ({"lock_version": 2}, "lock_version"),
    ],
    ids=[
        "commit-without-tree",
        "source-escapes",
        "bad-digest",
        "bad-version",
        "naive-time",
        "newer-lock-version",
    ],
)
def test_the_lock_is_refused(tmp_path, change, why):
    data = {
        "lock_version": change.pop("lock_version", 1),
        "addons": {"release@acme": {**LOCKED, **change}},
    }
    path = tmp_path / "addons.lock"
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(ConfigError, match=why):
        AddonsLock.load(path)


def test_the_lock_round_trips(tmp_path):
    """What `kraft admin addon` writes, it reads back unchanged."""
    lock = AddonsLock.model_validate({"addons": {"release@acme": LOCKED}})
    path = tmp_path / "addons.lock"
    write_yaml(path, lock.model_dump(mode="json"))
    assert AddonsLock.load(path) == lock


_REPO_LOCK = {
    "git": "https://example.com/app.git",
    "branch": "main",
    "commit": "a" * 40,
    "tree": "b" * 40,
    "digest": "sha256:" + "c" * 64,
    "updated_at": "2026-10-11T00:00:00Z",
}


@pytest.mark.parametrize(
    "change, why",
    [
        pytest.param({}, None, id="reads"),
        pytest.param({"commit": "main"}, "commit", id="commit-is-not-an-oid"),
        pytest.param({"extra": 1}, "extra", id="unknown-key"),
    ],
)
def test_the_repo_library_lock(tmp_path, change, why):
    path = tmp_path / "repo-libraries.lock"
    write_yaml(path, {"lock_version": 1, "repos": {"app": {**_REPO_LOCK, **change}}})

    if why is None:
        assert RepoLibrariesLock.load(path).repos["app"].branch == "main"
        assert RepoLibrariesLock.load(tmp_path / "absent.lock").repos == {}
    else:
        with pytest.raises(ConfigError, match=why):
            RepoLibrariesLock.load(path)
