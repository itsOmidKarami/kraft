"""Whose a note on a bot thread is, and the markers and hashes that tie Kraft's
own notes to the reply record."""

from __future__ import annotations

import hashlib

import pytest

from kraft.adapters.forge.models import ForgeNote
from kraft.adapters.forge.replies.facts import (
    Kraft,
    body_hash,
    marker_of,
    note_by,
    reply_marker,
    summary_marker,
    without_marker,
)


def _note(author="coderabbitai", type_="Bot", body="hi", id="N1", rest_id="1"):
    return ForgeNote(id, rest_id, author, type_, body, "t", "t")


ITEM = "w1abcdef"
MARK = reply_marker(f"{ITEM}-0123456789ab")
SIGNED = f"Declined: no.\n\n---\n_Posted by Kraft_\n\n{MARK}"


@pytest.mark.parametrize(
    "note, viewer, posted, by",
    [
        (_note(), None, {}, "bot"),
        (_note(author="coderabbitai[bot]"), None, {}, "bot"),
        (_note(author="other-bot"), None, {}, "bot"),  # another configured bot
        (_note(author="coderabbitai", type_="User"), None, {}, "person"),
        (_note(author="coderabbitai", type_=None), None, {}, "bot"),  # GitLab: no type
        (_note(author="a-person", type_="User"), None, {}, "person"),
        (_note(author="kraft-user", type_="User", body=SIGNED), None, {}, "person"),
        (_note(author="kraft-user", type_="User", body=SIGNED), "kraft-user", {}, "kraft"),
        (_note(author="a-person", type_="User", body=SIGNED), "kraft-user", {}, "person"),
        (
            _note(author="kraft-user", type_="User", body=SIGNED.replace(ITEM, "zzzzzzzz")),
            "kraft-user",
            {},
            "person",
        ),
        (
            _note(author="kraft-user", type_="User", body=SIGNED),
            "kraft-user",
            {"N1": body_hash(SIGNED)},
            "kraft",
        ),
        (
            _note(author="kraft-user", type_="User", body="edited\n\n" + MARK),
            "kraft-user",
            {"1": body_hash(SIGNED)},
            "person",
        ),
    ],
    ids=[
        "app-account",
        "bot-suffix",
        "another-configured-bot",
        "github-user-named-like-the-bot",
        "gitlab-login-decides",
        "a-person",
        "no-viewer-known",
        "the-viewers-marked-note",
        "a-marker-on-someone-elses-note",
        "another-items-marker",
        "recorded-and-unedited",
        "recorded-then-edited",
    ],
)
def test_whose_a_note_is(note, viewer, posted, by):
    assert (
        note_by(
            note,
            bots=("coderabbitai", "other-bot"),
            viewer=viewer,
            work_item_id=ITEM,
            posted=posted,
        )
        == by
    )


@pytest.mark.parametrize(
    "body, intent, bare",
    [
        (SIGNED, f"{ITEM}-0123456789ab", SIGNED.rsplit("\n", 1)[0].rstrip("\n")),
        (SIGNED + "\n", f"{ITEM}-0123456789ab", SIGNED.rsplit("\n", 1)[0].rstrip("\n")),
        (f"x\n{summary_marker(ITEM)}", None, "x"),
        (f"{MARK}\nquoted marker, not last", None, f"{MARK}\nquoted marker, not last"),
        ("plain", None, "plain"),
    ],
    ids=["reply", "trailing-newline", "summary", "marker-not-on-the-last-line", "none"],
)
def test_a_marker_is_read_off_the_last_line_and_left_out_of_the_hash(body, intent, bare):
    assert marker_of(body) == intent
    assert without_marker(body) == bare
    assert body_hash(body) == hashlib.sha256(bare.encode()).hexdigest()


def test_kraft_talks_in_a_thread_holding_its_note():
    mine = _note(author="kraft-user", type_="User", body=SIGNED, id="N2", rest_id="2")
    notes = (_note(), mine)

    assert Kraft("kraft-user", ITEM, {"N2": body_hash(SIGNED)}).talks_in(notes)
    assert not Kraft("kraft-user", ITEM, {"N2": body_hash("edited")}).talks_in(notes)
