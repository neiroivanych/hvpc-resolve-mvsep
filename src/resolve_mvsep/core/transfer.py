"""Persist byte progress at most twice a second, with per-attempt rate/ETA."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

from ..mvsep.models import TransferProgress
from .job import Job, now


class TransferReporter:
    def __init__(self, job: Job, path: Path, *, clock: Callable[[], float] = time.monotonic):
        self.job, self.path, self.clock = job, path, clock
        self.key = None
        self.started = self.last_saved = 0.0
        self.previous = 0
        self.total = None

    def __call__(self, event: TransferProgress) -> None:
        instant = self.clock()
        key = (event.direction, event.file_index, event.file_count)
        reset = key != self.key or event.transferred < self.previous or event.transferred == 0
        changed_total = event.total != self.total
        if reset:
            self.key, self.started = key, instant
        self.previous, self.total = event.transferred, event.total
        complete = event.total is not None and event.total > 0 and event.transferred >= event.total
        if not (reset or changed_total or complete) and instant - self.last_saved < 0.5:
            return
        self.last_saved = instant
        job = self.job
        job.transfer_direction = event.direction
        job.transfer_bytes, job.transfer_total = event.transferred, event.total
        job.transfer_file_index, job.transfer_file_count = event.file_index, event.file_count
        job.transfer_updated_at = now()
        elapsed = instant - self.started
        # Allow an initial sample window. A fresh retry/file never inherits an ETA.
        job.transfer_rate = event.transferred / elapsed if elapsed >= 2 and event.transferred > 0 else None
        job.transfer_eta = ((event.total - event.transferred) / job.transfer_rate
                            if job.transfer_rate and event.total and 0 < event.transferred < event.total else None)
        job.save(self.path)
