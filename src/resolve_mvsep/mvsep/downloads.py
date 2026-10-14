"""Bounded streaming downloads with safe names and no file overwrites."""

from __future__ import annotations

import http.client
import hashlib
import tempfile
import time
from pathlib import Path
from typing import Callable

from .errors import DeadlineExceeded, MVSEPError, NetworkError
from .models import RemoteFile
from .names import destination_name_limit, output_name, safe_name
from .transport import HTTPTransport, validate_url
from .streaming import read_block


def output_path(transport, remote, directory, index, source_stem=None, preset_output=False, run_index=None):
    remote_name = transport.redactor.clean(remote.filename)
    name_limit = destination_name_limit(directory)
    name = (output_name(transport.redactor.clean(source_stem), remote_name,
                        preset_output=preset_output, run_index=run_index, max_length=name_limit)
            if source_stem else safe_name(f'{index:02d}_' + safe_name(remote_name), name_limit))
    return directory / name


def file_record(path: Path) -> dict:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return {'path': str(path.resolve()), 'size': path.stat().st_size, 'sha256': digest.hexdigest()}


def reusable_file(transport, remote, directory, record, legacy_paths, expected_path, timeout):
    """Reuse only recorded completed files in this output directory, never a .part.

    New receipts verify SHA-256; older jobs also require the current server size
    to match a file that their manifest already recorded as completely downloaded.
    """
    validate_url(remote.url)
    if record:
        try:
            path = Path(record['path']).resolve()
            if (path.parent == directory.resolve() and path.is_file() and path.stat().st_size > 0
                    and file_record(path) == record):
                return path
        except (OSError, KeyError, TypeError, ValueError):
            pass
        return None  # a changed receipt must not be re-adopted through legacy size checks
    path = expected_path.resolve()
    if path not in legacy_paths or path.parent != directory.resolve() or not path.is_file() or path.stat().st_size <= 0:
        return None
    with transport.open('GET', remote.url, timeout) as response:
        kind = response.headers.get('Content-Type', '').lower()
        length = response.headers.get('Content-Length') or ''
        if length.isdecimal() and int(length) == path.stat().st_size and 'text/' not in kind and 'json' not in kind:
            return path
    return None


def download_file(transport: HTTPTransport, remote: RemoteFile, directory: Path, index: int,
                  *, request_timeout: float, deadline: float,
                  clock: Callable[[], float] = time.monotonic,
                  on_progress: Callable[[int, int | None], None] | None = None,
                  source_stem: str | None = None, preset_output: bool = False,
                  run_index: int | None = None) -> Path:
    validate_url(remote.url)
    directory.mkdir(parents=True, exist_ok=True)
    target = output_path(transport, remote, directory, index, source_stem, preset_output, run_index)
    for suffix in range(10000):
        copy = f"__copy{suffix}" if run_index is not None else f"_{suffix}"
        candidate = target if suffix == 0 else target.with_name(f"{target.stem}{copy}{target.suffix}")
        try:
            # Reserve our own destination; never replace an existing user/source file.
            with candidate.open("xb"):
                pass
            target = candidate
            break
        except FileExistsError:
            continue
    else:
        raise MVSEPError("Too many existing files with this output name.")
    temporary: Path | None = None
    try:
        if on_progress:
            on_progress(0, None)  # also resets a failed/retried file before connecting
        remaining = deadline - clock()
        if remaining <= 0:
            raise DeadlineExceeded("Download time limit reached. The remote job can be resumed.")
        with transport.open("GET", remote.url, min(request_timeout, remaining)) as response:
            content_type = response.headers.get("Content-Type", "").lower()
            if "text/" in content_type or "json" in content_type:
                raise MVSEPError("Download returned a text/error page instead of audio.")
            length = response.headers.get("Content-Length")
            expected = int(length) if length and length.isdecimal() else None
            received = 0
            if on_progress:
                on_progress(0, expected)
            with tempfile.NamedTemporaryFile(dir=directory, prefix=".stem-", suffix=".part", delete=False) as stream:
                temporary = Path(stream.name)
                while True:
                    if clock() >= deadline:
                        raise DeadlineExceeded("Download time limit reached. The remote job can be resumed.")
                    chunk = read_block(response, 256 * 1024, deadline=deadline,
                                       timeout=request_timeout, clock=clock,
                                       message='Download time limit reached. The remote job can be resumed.')
                    if not chunk:
                        break
                    stream.write(chunk)
                    received += len(chunk)
                    if on_progress:
                        on_progress(received, expected)
            if received == 0 or (expected is not None and received != expected):
                raise NetworkError("Incomplete or empty stem download; no result file was accepted.")
        temporary.replace(target)
        return target
    except (OSError, http.client.HTTPException):
        if clock() >= deadline:
            raise DeadlineExceeded('Download time limit reached. The remote job can be resumed.') from None
        raise NetworkError("Could not download/save the stem completely. Check disk space and the network.") from None
    finally:
        if temporary is None or temporary.exists():
            target.unlink(missing_ok=True)
            if temporary is not None:
                temporary.unlink(missing_ok=True)
