"""The harness guides run-slices reads, discover.md for a harness with none, and
the manifests other harnesses install the plugin from. A guide the skill does
not link is never read; a manifest that does not parse never installs."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
REFERENCES = PLUGIN / "skills" / "run-slices" / "references"
#: The harnesses that name skills without the plugin's prefix and set no
#: plugin-root variable.
UNNAMESPACED = ["gemini", "antigravity", "amp", "opencode"]


def _flat(path: Path) -> str:
    return " ".join(path.read_text().split())


def test_run_slices_links_every_file_in_its_references(texts):
    """A guide the skill does not link is one no controller reads."""
    linked = set(re.findall(r"\]\(references/([\w-]+\.md)\)", texts["run-slices"]))
    assert {path.name for path in REFERENCES.glob("*.md")} == linked


@pytest.mark.parametrize("path", sorted(REFERENCES.glob("*.md")), ids=lambda p: p.stem)
def test_a_reference_links_no_further_file(path):
    """References stay one level deep: a link out of one is a file the
    controller reads partly, or not at all."""
    assert not re.findall(r"\]\((?!https?:)[^)]+\.md\)", path.read_text())


@pytest.mark.parametrize(
    "needle",
    [
        "Read `.kraft-lite/harnesses/<harness>.md` if it exists",
        "When the version matches the one the file was checked on, use the file and do not "
        "research again. Re-check an ability when the version differs, or when one of its "
        "commands fails.",
        "Write `.kraft-lite/harnesses/<harness>.md` in a shipped guide's shape",
        "Every ability gets CONFIRMED or UNCONFIRMED, its source, and for UNCONFIRMED its "
        "fallback.",
        "Run only read-only commands: `--help`, `--version`, and list commands. Never guess a "
        "command or flag, and never test one by starting a real child",
        '`KL decide --question "Harness abilities for <harness>"',
        "Use only CONFIRMED abilities.",
        "offer the file as a shipped guide",
    ],
    ids=[
        "local-guide-first",
        "recheck-on-a-new-version-or-a-failure",
        "writes-the-local-guide",
        "each-ability-sourced-and-marked",
        "read-only-never-a-trial-child",
        "logs-it-with-decide",
        "uses-only-confirmed",
        "offers-it-to-ship",
    ],
)
def test_discover_says_how_to_find_and_keep_a_harness_s_abilities(needle):
    assert needle in _flat(REFERENCES / "discover.md")


def test_discover_looks_in_order():
    """The cheapest, surest source first: a tool the session already has, then
    Kraft's checked profile, then the CLI's own help, and the web last."""
    body = _flat(REFERENCES / "discover.md")
    order = [
        "Your own tool list, deferred tools included",
        "Kraft's harness profile",
        "`kraft admin harnesses <harness>`",
        "The harness CLI's `--help`, then each subcommand's",
        "The harness's official documentation",
    ]
    places = [body.index(step) for step in order]
    assert places == sorted(places)


@pytest.mark.parametrize("harness", UNNAMESPACED)
def test_a_guide_says_how_this_harness_names_skills_and_finds_kl(harness):
    """`kraft-lite:build-slice` is no skill's name here, and `$CLAUDE_PLUGIN_ROOT`
    is unset: a child told the first, or a controller trusting the second, fails."""
    body = _flat(REFERENCES / f"{harness}.md")
    assert (
        "Skills are not namespaced here: `kraft-lite:<name>` in the skills means the skill "
        "`<name>`, so a child's instructions say `build-slice`"
    ) in body
    assert "There is no plugin-root variable: `kl.py` is two directories above the skill file." in (
        body
    )


@pytest.mark.parametrize("harness", UNNAMESPACED)
def test_an_unnamespaced_guide_sends_the_user_to_run_slices_by_its_own_name(harness):
    """The fallback for a controller nothing wakes, under the name this harness
    gives the skill; the child's own wake-up stays a line of its own."""
    check_in = " ".join(
        (REFERENCES / f"{harness}.md").read_text().split("## Check in later")[1].split()
    )
    assert "tell the user to invoke `run-slices` to continue the run" in check_in
    assert "Child: scheduling its own wake-up is unconfirmed too" in check_in
    assert "`none; continue on the controller's check-in`" in check_in


@pytest.mark.parametrize(
    "harness, needle",
    [
        ("gemini", "gemini --skip-trust -m <model> --approval-mode yolo -o stream-json -p"),
        ("gemini", "Never run `gemini --delete-session`"),
        ("antigravity", "--dangerously-skip-permissions --output-format stream-json"),
        ("antigravity", "--conversation <conversation-id> --dangerously-skip-permissions"),
        ("amp", "amp --no-archive-after-execute -m medium --stream-json -x"),
        ("amp", "amp threads continue <thread-id> --no-archive-after-execute -x"),
        ("opencode", "opencode run --format json --auto -m <provider/model> --"),
        ("opencode", "Never run `opencode session delete`"),
    ],
    ids=[
        "gemini-starts-headless-and-trusted",
        "gemini-never-deletes",
        "antigravity-starts-with-permissions",
        "antigravity-messages-by-conversation",
        "amp-keeps-the-thread-open",
        "amp-continues-the-thread-open",
        "opencode-starts-auto",
        "opencode-never-deletes",
    ],
)
def test_a_guide_gives_the_profile_s_checked_command(harness, needle):
    """Each command is the one Kraft's harness profile checked against that CLI;
    without the flag named, a headless child is denied its tools or its thread
    is closed for good."""
    assert needle in _flat(REFERENCES / f"{harness}.md")


@pytest.mark.parametrize(
    "path",
    [
        ".claude-plugin/plugin.json",
        ".cursor-plugin/plugin.json",
        "gemini-extension.json",
        "plugin.json",
    ],
)
def test_every_manifest_parses_and_names_the_plugin(path):
    manifest = json.loads((PLUGIN / path).read_text())
    assert manifest["name"] == "kraft-lite"
    assert manifest["description"]


def test_the_antigravity_manifest_holds_only_the_keys_its_schema_allows():
    """The schema refuses any key it does not list, `version` included."""
    manifest = json.loads((PLUGIN / "plugin.json").read_text())
    assert set(manifest) <= {"$schema", "name", "description"}


def test_the_gemini_manifest_carries_the_release_version():
    """Gemini CLI requires a version; it moves with the other manifests."""
    gemini = json.loads((PLUGIN / "gemini-extension.json").read_text())
    claude = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text())
    assert gemini["version"] == claude["version"]
