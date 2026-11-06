from hashlib import sha256
from io import BytesIO

import pytest
from fastapi import UploadFile

from backend.services.uploads import inspect_upload


@pytest.mark.parametrize("size", [65535, 65536, 65537])
def test_upload_inspection_hashes_in_bounded_chunks_and_rewinds(size):
    class BoundedFile(BytesIO):
        _rolled = False  # Match an in-memory SpooledTemporaryFile.

        def read(self, length=-1):
            assert length == 65536
            return super().read(length)

    content = b"x" * size
    file = UploadFile(BoundedFile(content), filename="source.md")
    coroutine = inspect_upload(file, limit=65536)
    try:
        coroutine.send(None)
    except StopIteration as result:
        summary = result.value
    else:
        coroutine.close()
        raise AssertionError("In-memory upload unexpectedly waited for I/O")
    assert summary.name == "source.md" and summary.size == size
    assert summary.digest == sha256(content).hexdigest()
    assert summary.error == ("FILE_TOO_LARGE" if size > 65536 else None)
    assert file.file.tell() == 0
