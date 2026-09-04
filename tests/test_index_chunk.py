from __future__ import annotations

from kraft.index.chunk import CHUNK_OVERLAP, CHUNK_SIZE, chunk_markdown


def test_empty_input_yields_nothing():
    assert chunk_markdown("") == []
    assert chunk_markdown("   \n\n  ") == []


def test_short_document_is_one_chunk():
    text = "# Title\n\nA short paragraph.\n"
    assert chunk_markdown(text) == [text.strip()]


def test_headings_split_and_keep_their_heading():
    text = "# One\n\nbody of one\n\n# Two\n\nbody of two\n\n## Three\n\nbody of three\n"
    chunks = chunk_markdown(text)
    assert len(chunks) == 3
    assert chunks[0].startswith("# One") and "body of one" in chunks[0]
    assert chunks[1].startswith("# Two") and "body of two" in chunks[1]
    assert chunks[2].startswith("## Three") and "body of three" in chunks[2]


def test_preamble_before_the_first_heading_is_kept():
    chunks = chunk_markdown("intro prose\n\n# One\n\nbody\n")
    assert any("intro prose" in c for c in chunks)


def test_long_section_is_windowed_with_overlap():
    para = "x" * 300
    text = "# Big\n\n" + "\n\n".join([para] * 20)  # ~6000 chars
    chunks = chunk_markdown(text, size=1200, overlap=200)
    assert len(chunks) > 1
    # consecutive chunks share text: the tail of one appears in the next
    assert chunks[0][-100:] in chunks[1]


def test_chunks_are_non_empty_and_bounded():
    para = "y" * 400
    text = "# Big\n\n" + "\n\n".join([para] * 12)
    chunks = chunk_markdown(text, size=1200, overlap=200)
    assert all(c.strip() for c in chunks)
    # a chunk may overshoot by at most one paragraph, never unboundedly
    assert all(len(c) <= 1200 + 400 + 200 for c in chunks)


def test_a_single_paragraph_longer_than_the_window_still_splits():
    text = "# Big\n\n" + "z" * 5000
    chunks = chunk_markdown(text, size=1200, overlap=200)
    assert len(chunks) > 1
    assert all(len(c) <= 1200 + 200 for c in chunks)


def test_defaults_are_the_documented_ones():
    assert (CHUNK_SIZE, CHUNK_OVERLAP) == (1200, 200)
