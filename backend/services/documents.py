import unicodedata
from hashlib import sha256
from pathlib import PurePosixPath

MAX_FILE_BYTES = 30_000_000
EXTENSIONS = {".txt": "text/plain", ".md": "text/markdown", ".markdown": "text/markdown"}


class DocumentError(ValueError):
    def __init__(self, code, status=422):
        self.code, self.status = code, status
        super().__init__(code)


def validate_file(name: str, data: bytes):
    name = PurePosixPath(name.replace("\\", "/")).name
    name = "".join(c for c in name if c.isprintable() and c not in "\r\n")[:255]
    suffix = PurePosixPath(name).suffix.lower()
    if suffix not in EXTENSIONS:
        raise DocumentError("UNSUPPORTED_FORMAT")
    if len(data) > MAX_FILE_BYTES:
        raise DocumentError("FILE_TOO_LARGE", 413)
    try:
        content = data.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError as exc:
        raise DocumentError("INVALID_ENCODING") from exc
    content = unicodedata.normalize("NFC", content.replace("\r\n", "\n").replace("\r", "\n"))
    if not content.strip():
        raise DocumentError("EMPTY_DOCUMENT")
    if any(ord(c) < 32 and c not in "\n\t\f" for c in content):
        raise DocumentError("BINARY_DOCUMENT")
    return name, EXTENSIONS[suffix], content, sha256(data).digest()
