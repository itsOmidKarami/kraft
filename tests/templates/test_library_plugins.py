"""The template library with plugin layers: a plugin's declarations are
addressed as `<namespace>:<name>`, and a bare local name stays local."""

import pytest

from kraft import harness
from kraft.templates.environment import HarnessProfileTable, TemplateEnvironmentError
from kraft.templates.library import TemplateLibrary, TemplateLibraryError
from support.plugins import AGENT, chain, home


def _chain_id(tmp_path):
    TemplateLibrary.from_yaml_dir(
        home(tmp_path, chains={"ship": {**chain("base"), "id": "release:ship"}})
    )


def _library_key(tmp_path):
    TemplateLibrary.from_yaml_dir(home(tmp_path, library={"tasks": {"release:base": AGENT}}))


def _profile_id(tmp_path):
    HarnessProfileTable.from_mapping(
        {"profiles": {"release:deep": {"model": {"codex": "m"}}}},
        tmp_path / "harnesses.yaml",
        harnesses=harness.load(None).valid,
    )


@pytest.mark.parametrize(
    "load", [_chain_id, _library_key, _profile_id], ids=["chain-id", "library-key", "profile-id"]
)
def test_a_local_declaration_with_a_colon_is_refused(tmp_path, load):
    with pytest.raises(
        (TemplateLibraryError, TemplateEnvironmentError), match="declared bare|must match"
    ):
        load(tmp_path)
