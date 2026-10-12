import pytest

from app.chunking import chunk_text


def test_short_text_is_one_chunk():
    assert chunk_text("short text", size=100, overlap=10) == ["short text"]


def test_chunks_respect_size():
    text = " ".join(["word"] * 500)
    chunks = chunk_text(text, size=100, overlap=20)
    assert len(chunks) > 1
    assert all(len(c) <= 100 for c in chunks)


def test_does_not_split_words():
    text = " ".join(["abcdefgh"] * 100)
    for c in chunk_text(text, size=50, overlap=10):
        assert all(word == "abcdefgh" for word in c.split())


def test_overlap_repeats_content():
    text = " ".join(str(i) for i in range(200))
    chunks = chunk_text(text, size=60, overlap=20)
    first_tail = chunks[0].split()[-1]
    assert first_tail in chunks[1].split()


def test_text_without_spaces_still_progresses():
    chunks = chunk_text("x" * 1000, size=100, overlap=99)
    assert chunks and all(len(c) <= 100 for c in chunks)


@pytest.mark.parametrize("size,overlap", [(0, 0), (100, 100), (100, -1)])
def test_invalid_params(size, overlap):
    with pytest.raises(ValueError):
        chunk_text("text", size=size, overlap=overlap)
