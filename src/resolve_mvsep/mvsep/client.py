"""MVSEP create/get/poll/download API. Never imports DaVinci Resolve."""

from __future__ import annotations

import math
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode

from ..core.secrets import Redactor
from .downloads import download_file, output_path, reusable_file, file_record
from .errors import APIError, DeadlineExceeded, MVSEPError, NetworkError
from .error_details import error_details
from .models import (AccountAccess, DailyQuota, QueueSummary, RemoteFile, RemoteResult, SeparationSpec,
                     TransferProgress, PREPARATION_STATUSES, SEPARATION_STATUSES)
from .transport import HTTPTransport, MultipartBody, validate_url

API_ROOT = "https://mvsep.com/api"
ACTIVE_STATUSES = PREPARATION_STATUSES | SEPARATION_STATUSES


class MVSEPClient:
    def __init__(self, token: str, *, request_timeout: float = 60, poll_interval: float = 10,
                 max_get_retries: int = 3, transport: Any = None,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self._token = token
        self.redactor = Redactor(token)
        self.transport = transport or HTTPTransport(self.redactor)
        self.request_timeout = request_timeout
        self.poll_interval = poll_interval
        self.max_get_retries = max_get_retries
        self.clock, self.sleep = clock, sleep

    def _remaining(self, deadline: float) -> float:
        remaining = deadline - self.clock()
        if remaining <= 0:
            raise DeadlineExceeded("MVSEP wait timed out. The remote job may still be running; resume it using the saved job.")
        return min(self.request_timeout, remaining)

    def _retry_get(self, operation: Callable[[], Any], deadline: float, *, deadline_error: str | None = None) -> Any:
        for attempt in range(self.max_get_retries + 1):
            try:
                self._remaining(deadline)
                return operation()
            except DeadlineExceeded:
                if deadline_error:
                    raise DeadlineExceeded(deadline_error) from None
                raise
            except (NetworkError, APIError) as exc:
                if isinstance(exc, APIError) and not exc.retryable:
                    raise
                if attempt >= self.max_get_retries:
                    raise
                delay = max(min(2 ** attempt, 30), getattr(exc, "retry_after", 0))
                self.sleep(min(delay, max(0, deadline - self.clock())))
        raise AssertionError("unreachable")

    def _checked(self, payload: Any) -> dict:
        if not isinstance(payload, dict):
            raise MVSEPError("MVSEP returned an unexpected response structure.")
        if payload.get("success") is not True:
            raise APIError("MVSEP: " + (error_details(payload, self.redactor) or "Request rejected"))
        return payload

    def get_account_access(self) -> AccountAccess:
        """Read flags/balance only; never enable Premium or persist the user response."""
        query = urlencode({"api_token": self._token})
        payload = self._checked(self.transport.json("GET", API_ROOT + "/app/user?" + query,
                                                    min(10, self.request_timeout)))
        data = payload.get("data")
        if not isinstance(data, dict):
            raise MVSEPError("MVSEP returned invalid account access data.")
        flag = data.get("premium_enabled")
        enabled = True if flag in (1, "1", True) else False if flag in (0, "0", False) else None
        raw = data.get("premium_minutes")
        try:
            balance = float(raw) if not isinstance(raw, bool) else None
        except (TypeError, ValueError, OverflowError):
            balance = None
        if balance is not None and not math.isfinite(balance):
            balance = None
        return AccountAccess(enabled, balance)

    def get_daily_quota(self) -> DailyQuota:
        """Live /app/queue also returns free_separations (not listed in full_api)."""
        query = urlencode({"api_token": self._token})
        payload = self.transport.json("GET", API_ROOT + "/app/queue?" + query,
                                      min(8, self.request_timeout))
        data = payload.get("free_separations") if isinstance(payload, dict) and payload.get("success") is not False else None
        if isinstance(data, dict):
            left, maximum = data.get("left"), data.get("max")
            if type(left) is int and type(maximum) is int and 0 <= left <= maximum:
                return DailyQuota(left, maximum)
        raise MVSEPError("MVSEP did not return a valid daily quota.")

    def create_job(self, source: Path, spec: SeparationSpec, *,
                   on_progress: Callable[[TransferProgress], None] | None = None) -> str:
        if not self._token:
            raise MVSEPError("MVSEP API token is missing.")
        if not source.is_file() or source.stat().st_size == 0:
            raise MVSEPError("Source audio file is missing or empty.")
        fields = spec.form_fields()
        fields["api_token"] = self._token
        def report(sent: int, total: int) -> None:
            if on_progress:
                on_progress(TransferProgress("upload", sent, total))
        try:
            payload = self._checked(self.transport.json(
                "POST", API_ROOT + "/separation/create", self.request_timeout,
                MultipartBody(fields, source, report if on_progress else None)
            ))
        except (NetworkError, DeadlineExceeded):
            raise NetworkError(
                "Upload response was lost; the remote creation outcome is unknown. "
                "Creation was NOT retried. Check MVSEP history before submitting again."
            ) from None
        data = payload.get("data")
        task_hash = data.get("hash") if isinstance(data, dict) else None
        if (not isinstance(task_hash, str) or not task_hash or len(task_hash) > 1024
                or any(ord(c) < 32 for c in task_hash) or self.redactor.clean(task_hash) != task_hash):
            raise MVSEPError("MVSEP did not return a valid task hash. Check MVSEP history before submitting again.")
        return task_hash

    def get_result(self, task_hash: str, *, deadline: float) -> RemoteResult:
        # Token is needed for private preset results. mirror=0 avoids paid mirroring.
        query = urlencode({"hash": task_hash, "api_token": self._token, "mirror": "0"})
        url = API_ROOT + "/separation/get?" + query
        payload = self._checked(self._retry_get(
            lambda: self.transport.json("GET", url, self._remaining(deadline)), deadline
        ))
        raw_status = payload.get("status")
        if (not isinstance(raw_status, str) or not raw_status.strip() or len(raw_status) > 80
                or any(ord(char) < 32 for char in raw_status)):
            raise MVSEPError(f"Invalid MVSEP status field (type: {type(raw_status).__name__}). Polling stopped.")
        status = self.redactor.clean(raw_status).strip().lower()
        data = payload.get("data")
        # Some preparation responses have no fields yet (null or an empty array).
        if data is None or (isinstance(data, list) and not data):
            data = {}
        if not isinstance(data, dict):
            raise MVSEPError(f"Invalid MVSEP result data for status {status!r} (type: {type(data).__name__}). Polling stopped.")
        # A new intermediate phase must not strand a valid uploaded job. Only
        # explicit 'done' with validated files completes it. Polling retains its
        # deadline, and known failures still stop immediately.
        message = self.redactor.clean(data.get("message", ""))[:500]
        if status in {"failed", "not_found", "cancelled", "canceled", "error"}:
            raise APIError(f"MVSEP task {status}: {message}")
        files = []
        if status == "done":
            entries = data.get("files")
            if not isinstance(entries, list) or not entries:
                raise MVSEPError("MVSEP marked the job done but returned no output files.")
            for item in entries:
                if not isinstance(item, dict) or not isinstance(item.get("url"), str) or not isinstance(item.get("download"), str):
                    raise MVSEPError("Malformed output file entry; expected download and url fields.")
                files.append(RemoteFile(item["download"], validate_url(item["url"])))
        position = data.get("current_order")
        output_format = data.get("output_format")
        return RemoteResult(status, tuple(files), message,
                            self._count(position),
                            str(output_format) if output_format is not None else None,
                            self._count(data.get("queue_count")),
                            self._count(data.get("finished_chunks")),
                            self._count(data.get("all_chunks")))

    @staticmethod
    def _count(value: Any) -> int | None:
        return value if type(value) is int and value >= 0 else None

    def get_queue_summary(self, *, timeout: float = 3) -> QueueSummary:
        """Optional telemetry: one bounded GET, no retry delaying the main poll."""
        query = urlencode({"api_token": self._token})
        payload = self.transport.json("GET", API_ROOT + "/app/queue/summary?" + query,
                                      min(timeout, self.request_timeout))
        # The live API wraps the documented summary fields in {success, data}.
        if isinstance(payload, dict) and "success" in payload:
            payload = self._checked(payload).get("data")
        if (not isinstance(payload, dict) or payload.get("success") is False
                or "estimated_wait_seconds" not in payload):
            raise MVSEPError("Queue estimate unavailable.")
        seconds = payload.get("estimated_wait_seconds")
        if type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds < 0:
            seconds = None
        return QueueSummary(seconds, self._count(payload.get("ahead")))

    def wait_for_result(self, task_hash: str, *, timeout: float,
                        on_status: Callable[[RemoteResult], None] | None = None,
                        on_queue: Callable[[QueueSummary | None], None] | None = None) -> RemoteResult:
        deadline = self.clock() + timeout
        while True:
            result = self.get_result(task_hash, deadline=deadline)
            if on_status:
                on_status(result)
            if result.status == "done":
                return result
            if on_queue and result.status == "waiting":
                try:
                    summary = self.get_queue_summary(timeout=min(3, self._remaining(deadline)))
                except MVSEPError:
                    summary = None  # Optional estimate must never fail a separation.
                on_queue(summary)
            self.sleep(min(self.poll_interval, max(0, deadline - self.clock())))

    def download_results(self, result: RemoteResult, directory: Path, *, timeout: float,
                         on_file: Callable[[Path], None] | None = None,
                         on_progress: Callable[[TransferProgress], None] | None = None,
                         source_stem: str | None = None, preset_output: bool = False,
                         run_index: int | None = None, completed_files: dict | None = None,
                         legacy_paths: tuple[Path, ...] = ()) -> list[Path]:
        if result.status != "done" or not result.files:
            raise MVSEPError("There are no completed output files to download.")
        completed_files = completed_files if completed_files is not None else {}
        legacy_paths = {path.resolve() for path in legacy_paths}
        paths = []
        for index, remote in enumerate(result.files, 1):
            # Each file gets the full budget, including its retries. A slow first
            # stem must not consume the allowance for every remaining stem.
            deadline = self.clock() + timeout
            deadline_error = 'Download time limit reached. The remote job can be resumed.'
            key = remote.filename
            if sum(item.filename == key for item in result.files) != 1:
                key = f'{index}:{key}'
            def report(received: int, total: int | None) -> None:
                if on_progress:
                    on_progress(TransferProgress("download", received, total, index, len(result.files)))
            expected_path = output_path(self.transport, remote, directory, index, source_stem, preset_output, run_index)
            path = self._retry_get(lambda: reusable_file(
                self.transport, remote, directory, completed_files.get(key), legacy_paths, expected_path,
                self._remaining(deadline)), deadline, deadline_error=deadline_error)
            if path:
                report(path.stat().st_size, path.stat().st_size)
            else:
                path = self._retry_get(lambda: download_file(
                    self.transport, remote, directory, index, request_timeout=self.request_timeout,
                    deadline=deadline, clock=self.clock, on_progress=report if on_progress else None,
                    source_stem=source_stem, preset_output=preset_output, run_index=run_index,
                ), deadline, deadline_error=deadline_error)
            completed_files[key] = file_record(path)
            paths.append(path)
            if on_file:
                on_file(path)
        return paths

    def get_algorithms(self) -> list[dict]:
        deadline = self.clock() + self.request_timeout * (self.max_get_retries + 1)
        payload = self._retry_get(lambda: self.transport.json(
            "GET", API_ROOT + "/app/algorithms", self._remaining(deadline)), deadline)
        if not isinstance(payload, list) or any(not isinstance(item, dict) for item in payload):
            raise MVSEPError("MVSEP returned an invalid algorithm catalog.")
        return payload
