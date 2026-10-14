"""Persistent per-task configuration and state, ready for a future queue."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from uuid import uuid4

from ..mvsep.models import Mode, SeparationSpec


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobStatus(str, Enum):
    CONFIGURING = "configuring"
    CANCELLED = "cancelled"
    PENDING = "pending"
    PREPARING = "preparing"
    UPLOADING = "uploading"
    QUEUED = "queued"
    PROCESSING = "processing"
    DOWNLOADING = "downloading"
    IMPORTING = "importing"  # reserved for a later milestone
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class Job:
    source_file: str
    mode: Mode
    sep_type: int | None
    preset_id: int | None
    api_options: dict[str, str]
    output_format: int
    source_clip: dict = field(default_factory=dict)
    id: str = field(default_factory=lambda: uuid4().hex)
    status: JobStatus = JobStatus.PENDING
    progress: float | None = None  # The polling API does not promise a percentage.
    created_at: str = field(default_factory=now)
    updated_at: str = field(default_factory=now)
    remote_hash: str | None = None
    remote_status: str | None = None
    result_files: list[str] = field(default_factory=list)
    actual_output_format: str | None = None
    error: str | None = None
    phase: str = "M2"  # completed here means downloaded, NOT imported into Resolve.
    remote_message: str = ""
    remote_checked_at: str | None = None
    queue_position: int | None = None
    queue_count: int | None = None
    finished_chunks: int | None = None
    all_chunks: int | None = None
    account_wait_seconds: float | None = None
    account_ahead: int | None = None
    queue_checked_at: str | None = None
    expected_files: int | None = None
    stage_started_at: str | None = None
    transfer_direction: str | None = None
    transfer_bytes: int = 0
    transfer_total: int | None = None
    transfer_file_index: int = 1
    transfer_file_count: int = 1
    transfer_updated_at: str | None = None
    transfer_rate: float | None = None
    transfer_eta: float | None = None
    settings_profile: str | None = None
    source_sha256: str | None = None
    run_index: int | None = None
    settings_snapshot: dict | None = None
    output_relative: str | None = None
    output_root: str | None = None  # frozen local destination, never a server/profile parameter
    processing_started_at: str | None = None
    separation_completed_at: str | None = None
    remember_settings: bool = False
    post_import_target: str = 'none'  # local workflow only; never part of an MVSEP request/profile
    post_import_state: str | None = None
    downloaded_files: dict = field(default_factory=dict)  # completed-file paths, sizes and SHA-256; no remote URLs

    @classmethod
    def create(cls, source: Path, spec: SeparationSpec, metadata: dict | None = None) -> Job:
        return cls(str(source.resolve()), spec.mode, spec.sep_type, spec.preset_id,
                   dict(spec.api_options), spec.output_format, metadata or {})

    def specification(self) -> SeparationSpec:
        return SeparationSpec(self.mode, self.sep_type, self.preset_id, self.api_options, self.output_format)

    def save(self, path: Path) -> None:
        self.updated_at = now()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        # A Windows viewer/AV may briefly have the previous JSON open without
        # delete sharing. Do not fail a remote job because its UI just read it.
        for attempt in range(10):
            try:
                temporary.replace(path)
                break
            except PermissionError:
                if attempt == 9:
                    raise
                time.sleep(0.02)

    @classmethod
    def load(cls, path: Path) -> Job:
        data = json.loads(path.read_text(encoding="utf-8"))
        data["mode"] = Mode(data["mode"])
        data["status"] = JobStatus(data["status"])
        job = cls(**data)
        if len(job.id) != 32 or any(c not in "0123456789abcdef" for c in job.id):
            raise ValueError("Invalid job ID.")
        job.specification()
        return job
