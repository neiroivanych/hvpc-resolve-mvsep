"""One separation at a time; durable state before and after remote operations."""

from __future__ import annotations

import logging
from pathlib import Path

from ..mvsep.client import MVSEPClient
from ..mvsep.models import QueueSummary, RemoteResult
from ..mvsep.names import destination_name_limit
from .errors import InspectionError
from .i18n import tr
from .job import Job, JobStatus, now
from .history import reserve_run
from .lock import SingleJobLock
from .mvsep_config import MVSEPSettings
from .output import assign_output_directory, output_directory, output_base, prepare_output_root
from .recipe import capture_settings, write_reports
from .setup import cached_algorithms, freeze_defaults
from .transfer import TransferReporter

DIRECT_AUDIO_FORMATS = {".wav", ".mp3", ".flac"}
VIDEO_FORMATS = {".mp4"}


def validate_source(source: Path, properties: dict | None = None) -> None:
    if not source.is_absolute() or not source.is_file() or source.stat().st_size == 0:
        raise InspectionError("Source audio must be an existing, non-empty absolute file.")
    if source.suffix.lower() not in DIRECT_AUDIO_FORMATS | VIDEO_FORMATS:
        raise InspectionError(tr('Поддерживаются WAV, MP3, FLAC и MP4 со звуком.'))
    if source.suffix.lower() in VIDEO_FORMATS and properties is not None:
        try:
            has_audio = int(properties.get('Audio Ch', 0)) > 0
        except (TypeError, ValueError):
            has_audio = False
        if not has_audio:
            raise InspectionError(tr('Resolve не обнаружил звуковую дорожку в MP4. Выберите видео со встроенным звуком.'))


def job_path(root: Path, job: Job) -> Path:
    return root / "jobs" / job.id / "job.json"


def execute_job(root: Path, job: Job, settings: MVSEPSettings, logger: logging.Logger,
                client: MVSEPClient | None = None) -> Job:
    client = client or MVSEPClient(settings.api_token, request_timeout=settings.request_timeout_seconds,
                                   poll_interval=settings.poll_interval_seconds,
                                   max_get_retries=settings.max_get_retries)
    path = job_path(root, job)

    def set_status(status: JobStatus) -> None:
        if status != job.status or not job.stage_started_at:
            job.stage_started_at = now()
        job.status = status
        job.save(path)
        logger.info("Job %s: %s", job.id, status.value)

    def on_status(result: RemoteResult) -> None:
        if job.remote_status != result.status:
            job.stage_started_at = now()
        job.remote_status = result.status
        job.actual_output_format = result.output_format
        job.remote_message = client.redactor.clean(result.message)
        job.remote_checked_at = now()
        job.queue_position, job.queue_count = result.queue_position, result.queue_count
        job.finished_chunks, job.all_chunks = result.finished_chunks, result.all_chunks
        job.account_wait_seconds = job.account_ahead = None
        job.queue_checked_at = None
        if result.files:
            job.expected_files = len(result.files)
        set_status(JobStatus.QUEUED if result.status == "waiting" else
                   JobStatus.DOWNLOADING if result.status == "done" else JobStatus.PROCESSING)
        logger.info("MVSEP status: %s | queue position: %s | message: %s",
                    job.remote_status, result.queue_position, job.remote_message)

    def on_queue(summary: QueueSummary | None) -> None:
        job.account_wait_seconds = summary.estimated_wait_seconds if summary else None
        job.account_ahead = summary.ahead if summary else None
        job.queue_checked_at = now() if summary else None
        job.save(path)

    def on_file(file_path: Path) -> None:
        if str(file_path) not in job.result_files:
            job.result_files.append(str(file_path))
        job.save(path)
        write_reports(root, job)
        logger.info("Downloaded: %s", file_path)

    # Acquire before touching state. A competing process never rewrites an
    # active job's manifest, even when both invocations point to the same job.
    with SingleJobLock(root / "jobs" / ".active.lock"):
        # Also makes directly launched CLI jobs discoverable by the menu.
        from .active import record_worker
        record_worker(root, job)
        try:
            # Re-read under the lock in case another worker already completed it.
            if path.is_file():
                persisted = Job.load(path)
                job = persisted
            if job.status == JobStatus.COMPLETED:
                try:
                    write_reports(root, job)
                except OSError:
                    logger.warning("Files already downloaded; settings reports could not be refreshed.", exc_info=True)
                logger.info("Job already completed; no upload repeated.")
                return job
            if not job.remote_hash and job.status != JobStatus.PENDING:
                raise InspectionError("This job has no saved remote hash and may already have been uploaded. Check MVSEP history; no upload repeated.")
            job.error = None
            transfer = TransferReporter(job, path)
            if not job.remote_hash:
                from .access import check_access
                job.processing_started_at = now()
                set_status(JobStatus.PREPARING)
                validate_source(Path(job.source_file), job.source_clip.get('source', {}).get('properties'))
                prepare_output_root(output_base(root, job))
                check_access(job.specification(), cached_algorithms(root), client)
                if job.settings_snapshot is None:
                    frozen = freeze_defaults(job.specification(), cached_algorithms(root))
                    job.api_options = dict(frozen.api_options)
                    job.settings_snapshot = capture_settings(root, job)
                reserve_run(root, job)
                assign_output_directory(root, job)
                # Check the complete source/attempt path before a paid submission,
                # rather than discovering Windows path limits after separation.
                destination = output_directory(root, job)
                destination_name_limit(destination)
                prepare_output_root(destination)
                job.save(path)
                write_reports(root, job)
                logger.info("Source: %s | mode: %s | sep_type: %s | preset: %s | output_format: %s",
                            job.source_file, job.mode.value, job.sep_type, job.preset_id, job.output_format)
                set_status(JobStatus.UPLOADING)
                job.remote_hash = client.create_job(Path(job.source_file), job.specification(), on_progress=transfer)
                logger.info("MVSEP job created: %s", job.remote_hash)
                set_status(JobStatus.QUEUED)
            else:
                if job.settings_snapshot is None:
                    job.settings_snapshot = capture_settings(root, job, historical=True)
                    job.save(path)
                logger.info("Resuming MVSEP hash: %s; original per-job algorithm retained.", job.remote_hash)
            result = client.wait_for_result(job.remote_hash, timeout=settings.job_timeout_seconds,
                                            on_status=on_status, on_queue=on_queue)
            paths = client.download_results(result, output_directory(root, job),
                                    timeout=settings.download_timeout_seconds, on_file=on_file, on_progress=transfer,
                                    source_stem=Path(job.source_file).stem, preset_output=job.preset_id is not None,
                                    run_index=job.run_index, completed_files=job.downloaded_files,
                                    legacy_paths=tuple(Path(value) for value in job.result_files))
            job.result_files = [str(value) for value in paths]
            write_reports(root, job)
            job.progress = 1.0
            job.separation_completed_at = now()
            set_status(JobStatus.COMPLETED)
            logger.info("M2 completed: files downloaded. Resolve import is available as a separate explicit action.")
            return job
        except Exception as exc:
            job.error = client.redactor.clean(exc)
            job.progress = None
            set_status(JobStatus.FAILED)
            logger.exception("M2 job failed")
            raise
