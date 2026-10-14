"""OS-backed single-job lock. Released automatically if the worker exits/crashes."""

from __future__ import annotations

import os
from pathlib import Path
from types import TracebackType
from typing import BinaryIO

from .errors import InspectionError


class SingleJobLock:
    def __init__(self, path: Path):
        self.path = path
        self._stream: BinaryIO | None = None

    def __enter__(self) -> SingleJobLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stream = self.path.open("a+b")
        try:
            stream.seek(0, 2)
            if stream.tell() == 0:
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            stream.close()
            raise InspectionError("Another MVSEP job is already running. Wait for it to finish.") from None
        self._stream = stream
        return self

    def __exit__(self, exc_type: type | None, exc: BaseException | None, tb: TracebackType | None) -> None:
        if self._stream is not None:
            self._stream.close()
            self._stream = None
