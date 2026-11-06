from math import inf, nan

import pytest
from backend.services.documents import DocumentError, validate_file
from backend.workers.ingest import source_chunks, vector_text
from hypothesis import given, settings
from hypothesis import strategies as st

CONFIG = dict(
    parent_max_tokens=1200, child_target_tokens=160, token_estimate_ratio=1.3, overlap_ratio=0.12
)


def test_DAT_06_strict_utf8_and_markdown():
    data = b"\xef\xbb\xbf# Titulo\r\nTexto"
    name, mime, content, hashed = validate_file("tema.markdown", data)
    assert mime == "text/markdown" and content == "# Titulo\nTexto"
    assert len(hashed) == 32
    with pytest.raises(DocumentError, match="INVALID_ENCODING"):
        validate_file("tema.txt", b"\xff")


@pytest.mark.parametrize(
    "data,code",
    [(b"", "EMPTY_DOCUMENT"), (b" \n\t", "EMPTY_DOCUMENT"), (b"%PDF\x00", "BINARY_DOCUMENT")],
)
def test_invalid_material(data, code):
    with pytest.raises(DocumentError, match=code):
        validate_file("tema.txt", data)


def test_DAT_05_decimal_byte_boundary():
    assert validate_file("a.txt", b"a" * 30_000_000)[2]
    with pytest.raises(DocumentError, match="FILE_TOO_LARGE"):
        validate_file("a.md", b"a" * 30_000_001)


@given(st.text(alphabet=st.characters(blacklist_categories=("Cs",)), min_size=1, max_size=5000))
@settings(max_examples=100)
def test_source_offsets_preserve_unicode(content):
    for parent in source_chunks(content, CONFIG):
        assert parent["text"] in content
        for a, b, body in parent["children"]:
            assert parent["text"][a:b] == body
            assert 0 <= a < b <= len(parent["text"])


@pytest.mark.parametrize("vector", [[0, 0], [nan, 1], [inf, 1], [1], [1, 2, 3], [True, 1]])
def test_invalid_vectors(vector):
    with pytest.raises(ValueError, match="INVALID_VECTOR"):
        vector_text(vector, 2)


def test_vector_normalization():
    assert vector_text([3, 4], 2) == "[0.6,0.8]"


def test_large_finite_vectors_normalize_without_overflow():
    import json

    vector = json.loads(vector_text([1e308, 1e308], 2))
    assert sum(x * x for x in vector) == pytest.approx(1)
