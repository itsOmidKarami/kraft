"""`config/plugins.yaml` and `config/plugins.lock`: what each refuses, and why."""

import pytest
import yaml

from kraft.config import ConfigError, write_yaml
from kraft.plugins.config import PluginsConfig, PluginsLock

URL = "https://github.com/acme/kraft-plugins.git"
ACME = {"acme": {"git": URL}}
LOCAL = {"local": {"path": "/home/omid/src/my-plugins"}}
OID = "3f2a9c" + "0" * 34


def _load(tmp_path, text):
    path = tmp_path / "plugins.yaml"
    path.write_text(text if isinstance(text, str) else yaml.safe_dump(text))
    return PluginsConfig.load(path)


@pytest.mark.parametrize(
    ("data", "why"),
    [
        ({"collections": ACME, "extra": 1}, "extra"),
        ({"collections": ACME, "plugins": {"release@acme": {"as_": "rel"}}}, "as_"),
        ({"collections": {"acme": {"git": URL, "path": "/x"}}}, "exactly one of git or path"),
        ({"collections": {"acme": {}}}, "exactly one of git or path"),
        ({"collections": {"acme": {"git": "acme/kraft-plugins"}}}, "git must be a full URL"),
        ({"collections": {"acme": {"git": "http://github.com/a/b.git"}}}, "git must be a full URL"),
        (
            {"collections": {"acme": {"git": "https://u:tok@github.com/a/b.git"}}},
            "git must be a full URL",
        ),
        ({"collections": {"acme": {"git": "-u@host:repo"}}}, "git must be a full URL"),
        ({"collections": {"local": {"path": "plugins"}}}, "path must be an absolute directory"),
        ({"collections": {"local": {"path": "/a/../b"}}}, "path must be an absolute directory"),
        ({"collections": {"acme": {"git": URL, "ref": "--upload-pack=x"}}}, "starts with '-'"),
        ("collections:\n  acme:\n    git: " + URL + "\n    ref: 1.10\n", "quote it"),
        ({"collections": ACME, "plugins": {"release@other": True}}, "no collection 'other'"),
        (
            {"collections": ACME, "plugins": {"release@acme": True, "x@acme": {"as": "release"}}},
            "namespace 'release' is taken",
        ),
        ({"collections": ACME, "plugins": {"x@acme": {"as": "kraft"}}}, "Kraft's own namespace"),
        (
            {
                "collections": {"acme": {"git": URL, "auto_update": True}},
                "plugins": {"release@acme": {"auto_update": False}},
            },
            "auto-updates every plugin",
        ),
        ({"collections": {"local": {**LOCAL["local"], "auto_update": True}}}, "never auto-updates"),
        (
            {"collections": LOCAL, "plugins": {"tools@local": {"auto_update": True}}},
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
        "directory-plugin-auto-update",
    ],
)
def test_plugins_yaml_is_refused(tmp_path, data, why):
    with pytest.raises(ConfigError, match=why):
        _load(tmp_path, data)


@pytest.mark.parametrize(
    "data",
    [
        {"collections": ACME, "plugins": {"release@acme": True, "legacy@acme": False}},
        "collections:\nplugins:\n",
        {"collections": {"acme": {"git": URL, "ref": None}}},
        {"collections": {"acme": {"git": "ssh://git@host.example:2222/acme/x.git"}}},
        {"collections": {"acme": {"git": "git@github.com:acme/x.git"}}},
        {"collections": {"acme": {"git": URL, "ref": OID}}},
    ],
    ids=["short-forms", "empty-sections", "explicit-nulls", "ssh-port", "scp-like", "ref-commit"],
)
def test_plugins_yaml_accepts(tmp_path, data):
    loaded = _load(tmp_path, data)
    for plugin_id in loaded.plugins:
        assert loaded.namespace(plugin_id) == plugin_id.split("@")[0]


LOCKED = {
    "namespace": "release",
    "ref": "main",
    "commit": OID,
    "source": "./plugins/release",
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
        "plugins": {"release@acme": {**LOCKED, **change}},
    }
    path = tmp_path / "plugins.lock"
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(ConfigError, match=why):
        PluginsLock.load(path)


def test_the_lock_round_trips(tmp_path):
    """What `kraft admin plugin` writes, it reads back unchanged."""
    lock = PluginsLock.model_validate({"plugins": {"release@acme": LOCKED}})
    path = tmp_path / "plugins.lock"
    write_yaml(path, lock.model_dump(mode="json"))
    assert PluginsLock.load(path) == lock
