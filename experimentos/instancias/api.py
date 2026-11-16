"""Cliente HTTP para las pruebas que usan la API de TutorIA."""
import json
import mimetypes
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4


class ErrorApi(RuntimeError):
    def __init__(self, path, status):
        self.status = status
        super().__init__(f"{path}: HTTP {status}")


class Api:
    def __init__(self, base):
        self.base = base.rstrip("/") + "/api/v1"
        self.token = ""

    def call(self, path, *, payload=None, upload=None, timeout=10):
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        data = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload).encode()
        if upload is not None:
            filename, content = upload
            filename = filename.replace('"', "_").replace("\r", "_").replace("\n", "_")
            mime_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
            boundary = uuid4().hex
            headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
            part = (
                f'--{boundary}\r\nContent-Disposition: form-data; name="files"; filename="{filename}"\r\n'
                f"Content-Type: {mime_type}\r\n\r\n"
            )
            data = part.encode() + content + f"\r\n--{boundary}--\r\n".encode()
        if data is not None:
            headers["Idempotency-Key"] = str(uuid4())
        request = Request(self.base + path, data=data, headers=headers)
        try:
            with urlopen(request, timeout=timeout) as response:
                return json.load(response)
        except HTTPError as error:
            raise ErrorApi(path, error.code) from error
