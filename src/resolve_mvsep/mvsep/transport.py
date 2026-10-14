"""Standard-library HTTPS transport with streamed multipart uploads."""

from __future__ import annotations

import http.client
import json
import mimetypes
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Iterator
from urllib.parse import urljoin, urlsplit
from uuid import uuid4

from ..core.secrets import Redactor
from .errors import APIError, DeadlineExceeded, MVSEPError, NetworkError
from .error_details import error_details
from .streaming import read_limited


def validate_url(url: str) -> str:
    """Credentials/downloads stay on the HTTPS hosts documented by MVSEP."""
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        valid = (parts.scheme == "https" and (host == "mvsep.com" or host.endswith(".mvsep.com"))
                 and parts.port in (None, 443) and not parts.username and not parts.password)
    except (TypeError, ValueError):
        valid = False
    if not valid:
        raise MVSEPError("MVSEP returned an unsupported URL. Only HTTPS mvsep.com hosts are accepted.")
    return url


class SafeRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> Any:
        validate_url(urljoin(req.full_url, newurl))
        if req.get_method() == "POST":
            if code not in (307, 308):
                raise MVSEPError("Unexpected redirect during upload; creation was not retried. Check MVSEP history.")
            return urllib.request.Request(newurl, data=req.data, headers=dict(req.header_items()), method="POST")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class MultipartBody:
    """Re-iterable for documented HTTP redirects, without buffering the audio."""

    def __init__(self, fields: dict[str, str], source: Path,
                 on_progress: Callable[[int, int], None] | None = None):
        self.source = source
        self.on_progress = on_progress
        self.source_stat = source.stat()
        self.audio_length = self.source_stat.st_size
        self.boundary = "resolve-mvsep-" + uuid4().hex
        chunks = []
        for name, value in fields.items():
            chunks.append((f'--{self.boundary}\r\nContent-Disposition: form-data; name="{name}"'
                           f'\r\n\r\n{value}\r\n').encode("utf-8"))
        # The local filename is already stored in job metadata; a fixed upload
        # filename avoids Unicode/header quoting ambiguities on the server.
        name = "source" + source.suffix.lower()
        mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
        chunks.append((f'--{self.boundary}\r\nContent-Disposition: form-data; name="audiofile"; '
                       f'filename="{name}"\r\nContent-Type: {mime}\r\n\r\n').encode("ascii"))
        self._prefix = b"".join(chunks)
        self._suffix = f"\r\n--{self.boundary}--\r\n".encode("ascii")
        self.content_length = len(self._prefix) + self.audio_length + len(self._suffix)

    def __iter__(self) -> Iterator[bytes]:
        sent = 0
        if self.on_progress:
            self.on_progress(0, self.audio_length)
        with self.source.open("rb") as stream:
            def unchanged():
                current = os.fstat(stream.fileno())
                return all(getattr(current, key) == getattr(self.source_stat, key)
                           for key in ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns'))
            if not unchanged():
                raise NetworkError('Source file changed before upload. Check MVSEP history before submitting again.')
            yield self._prefix
            while sent < self.audio_length:
                chunk = stream.read(min(256 * 1024, self.audio_length - sent))
                if not chunk:
                    raise NetworkError('Source file changed during upload. Check MVSEP history before submitting again.')
                yield chunk
                # http.client resumes iteration after sending this chunk. Counting
                # before yield would report bytes merely read from the source file.
                sent += len(chunk)
                if self.on_progress:
                    self.on_progress(sent, self.audio_length)
            if not unchanged():
                raise NetworkError('Source file changed during upload. Check MVSEP history before submitting again.')
        yield self._suffix


class HTTPTransport:
    def __init__(self, redactor: Redactor, opener: Any = None, *, clock=time.monotonic):
        self.redactor = redactor
        self.opener = opener or urllib.request.build_opener(SafeRedirects())
        self.clock = clock

    def open(self, method: str, url: str, timeout: float, body: MultipartBody | None = None) -> Any:
        validate_url(url)
        headers = {"User-Agent": "HVPC-Resolve-MVSEP/0.2", "Accept-Encoding": "identity"}
        if body is not None:
            headers["Content-Type"] = f"multipart/form-data; boundary={body.boundary}"
            headers["Content-Length"] = str(body.content_length)
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            return self.opener.open(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            message = f"MVSEP HTTP {exc.code}"
            try:
                payload = json.loads(read_limited(exc, 65536, deadline=self.clock() + min(timeout, 5),
                                                  timeout=timeout, clock=self.clock))
                detail = error_details(payload, self.redactor)
                if detail:
                    message += ": " + detail
            except (OSError, ValueError, http.client.HTTPException, DeadlineExceeded):
                pass
            try:
                retry_after = min(60, max(0, float(exc.headers.get("Retry-After", "0"))))
            except (TypeError, ValueError):
                retry_after = 0
            code = exc.code
            exc.close()
            raise APIError(message, status_code=code, retry_after=retry_after) from None
        except (urllib.error.URLError, OSError, http.client.HTTPException):
            # Raw urllib exceptions can contain the token-bearing query string.
            raise NetworkError("MVSEP network/TLS/timeout error. Check the connection and try resuming the job.") from None

    def json(self, method: str, url: str, timeout: float, body: MultipartBody | None = None) -> Any:
        deadline = self.clock() + timeout
        try:
            with self.open(method, url, timeout, body) as response:
                # Uploading a large file may take longer than one socket timeout;
                # allow the response its own bounded budget after that upload.
                if body is not None:
                    deadline = self.clock() + timeout
                raw = read_limited(response, 4 * 1024 * 1024 + 1, deadline=deadline,
                                   timeout=timeout, clock=self.clock)
            if len(raw) > 4 * 1024 * 1024:
                raise MVSEPError("MVSEP JSON response is too large.")
            return json.loads(raw)
        except (UnicodeError, ValueError):
            raise MVSEPError("MVSEP returned invalid JSON; no response body was logged.") from None
        except (OSError, http.client.HTTPException):
            raise NetworkError("MVSEP response could not be read completely.") from None
