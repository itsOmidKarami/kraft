"""libyaml's loader and dumper when PyYAML has them, else the pure-Python pair.

The bytes are the same (compared file by file on everything
`harness.fake_templates_dir` writes) and it is ~8x faster: a templates dir parses
and dumps the shipped library and agent profiles ten times, for every test that
takes a `client`. Only the helpers that seed a library use it; the product's own
YAML reads are untouched."""

from __future__ import annotations

import yaml

_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
_DUMPER = getattr(yaml, "CSafeDumper", yaml.SafeDumper)


def load(text: str):
    return yaml.load(text, Loader=_LOADER)


def dump(data) -> str:
    return yaml.dump(data, Dumper=_DUMPER, sort_keys=False)
