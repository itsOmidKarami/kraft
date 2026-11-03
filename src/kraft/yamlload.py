"""Parse a YAML file the operator owns with libyaml where PyYAML has it.

The same data as `yaml.safe_load`, about ten times quicker: the config a server
start reads (library, harnesses, policy, repos) is parsed by pure Python
otherwise, and that is most of a start. A document libyaml refuses is read
again by the pure-Python parser, so the error a person sees is the one they
have always seen. Never for a file a repository or an add-on's author wrote:
those go through `config.bounded_yaml`.
"""

from __future__ import annotations

import yaml

_FAST = getattr(yaml, "CSafeLoader", None)
#: libyaml's composer recurses on the C stack: past ~20,000 levels (far fewer on a small thread
#: stack) it kills the process where the pure-Python parser raises RecursionError. Nesting is
#: never deeper than the brackets and dashes in the text, and no real config has this many.
_MAX_OPENERS = 500


def safe_load(text: str) -> object:
    if _FAST is not None and text.count("[") + text.count("{") + text.count("- ") <= _MAX_OPENERS:
        try:
            return yaml.load(text, _FAST)  # noqa: S506 -- a SafeLoader
        except yaml.YAMLError:
            pass
    return yaml.safe_load(text)
