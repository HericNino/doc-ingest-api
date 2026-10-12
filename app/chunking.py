def chunk_text(text: str, size: int, overlap: int) -> list[str]:
    """Split text into chunks of at most `size` characters with `overlap` characters shared.

    Where possible it cuts on a space (within the last 20% of the window) so words
    are not split. This is the "chunk" step of crack -> chunk -> vectorize -> index.
    """
    if size <= 0:
        raise ValueError("size must be > 0")
    if not 0 <= overlap < size:
        raise ValueError("overlap must be >= 0 and smaller than size")

    text = text.strip()
    n = len(text)
    chunks: list[str] = []
    start = 0

    while start < n:
        end = min(start + size, n)
        if end < n:
            cut = text.rfind(" ", start + int(size * 0.8), end)
            if cut > start:
                end = cut
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= n:
            break
        # max(...) guarantees progress even with a large overlap
        next_start = max(end - overlap, start + 1)
        # If the overlap landed mid-word, move forward to the start of the next word.
        if next_start < end and text[next_start - 1] != " ":
            space = text.find(" ", next_start, end)
            if space != -1:
                next_start = space + 1
        start = next_start

    return chunks
