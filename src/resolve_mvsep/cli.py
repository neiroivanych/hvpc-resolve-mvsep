"""Separate download, monitoring and explicitly requested Resolve import actions."""

from __future__ import annotations

from .core.i18n import tr, language, load_language

import argparse
import json
from pathlib import Path

from .core.feedback import show_message
from .core.error_copy import error_text
from .core.job import Job, JobStatus
from .core.logging import close_logger, create_logger
from .core.mvsep_config import load_mvsep_settings
from .core.output import output_directory
from .core.runner import execute_job, job_path, validate_source
from .mvsep.client import MVSEPClient


def show_failed_job(root: Path, job: Job | None) -> bool:
    """Prefer the complete recovery UI to a modal dialog that keeps the worker alive."""
    if job is None:
        return False
    try:
        path = job_path(root, job)
        saved = Job.load(path)
        if saved.status != JobStatus.FAILED or not saved.error:
            return False  # do not hide configuration/monitor errors behind an unrelated result
        from .separation import show_progress
        return show_progress(root, path, notify_failure=False)
    except (OSError, ValueError, TypeError, KeyError):
        return False


def main(root: Path, argv: list[str] | None = None) -> int:
    language.set(load_language(root))
    parser = argparse.ArgumentParser(description="HVPC Resolve-MVSEP M2: whole audio file -> MVSEP -> local stems")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--file", type=Path, help="Upload a complete WAV/MP3/FLAC file")
    action.add_argument("--inspection", type=Path, help="Upload the whole source from a saved M1 JSON report")
    action.add_argument("--run-job", type=Path, help="Run a pending job, or resume its saved remote hash")
    action.add_argument("--resume", type=Path, help="Resume a job with a known hash; never re-upload")
    action.add_argument("--algorithms", action="store_true", help="Read the public algorithm catalog; no token required")
    action.add_argument("--check-config", action="store_true", help="Validate local settings; no network request")
    action.add_argument("--status", type=Path, help="Show the saved job state without contacting MVSEP")
    action.add_argument("--monitor", type=Path, help="Open local progress window; no upload or API call")
    action.add_argument("--import-job", type=Path, help="Import an already downloaded result into Resolve; no MVSEP request")
    action.add_argument('--resolve-status', type=Path, help='Read-only connection check for a saved job; no import or MVSEP call')
    parser.add_argument("--import-target", choices=('media_pool', 'timeline'), default='media_pool')
    parser.add_argument("--notify", action="store_true", help="Show a Windows completion/error dialog")
    parser.add_argument("--configure", action="store_true", help="Choose settings before running a new --run-job")
    args = parser.parse_args(argv)
    if args.configure and not args.run_job:
        parser.error("--configure requires --run-job")
    logger = None
    job = None
    client = None
    try:
        if args.resolve_status:
            from dataclasses import asdict
            from .resolve.status import inspect_connection
            from .resolve_monitor import STATUS_PREFIX
            saved = Job.load(args.resolve_status)
            if args.resolve_status.resolve() != job_path(root, saved).resolve():
                raise ValueError('Job must belong to this checkout.')
            print(STATUS_PREFIX + json.dumps(asdict(inspect_connection(saved))))
            return 0
        if args.import_job:
            from .import_results import run_import
            return run_import(root, args.import_job.resolve(), args.import_target)
        if args.monitor:
            from .progress_window import show_progress_window
            return show_progress_window(args.monitor)
        if args.status:
            saved = Job.load(args.status)
            print(f"Job {saved.id}: {saved.status.value}\nRemote status: {saved.remote_status}")
            print(f"Downloaded files: {len(saved.result_files)}\nError: {saved.error or 'none'}")
            return 0
        settings = load_mvsep_settings(root, require_token=not args.algorithms)
        logger, log_path = create_logger(root, prefix="m2", secrets=(settings.api_token,))
        client = MVSEPClient(settings.api_token, request_timeout=settings.request_timeout_seconds,
                             poll_interval=settings.poll_interval_seconds, max_get_retries=settings.max_get_retries)
        if args.check_config:
            print(f"Configuration OK. Token present (hidden). Mode: {settings.spec.mode.value}; "
                  f"sep_type: {settings.spec.sep_type}; preset: {settings.spec.preset_id}.")
            return 0
        if args.algorithms:
            from .core.catalog import refresh_catalog
            algorithms = refresh_catalog(root, client)
            catalog_path = root / "logs" / "mvsep_algorithms.json"
            print(f"Fetched {len(algorithms)} algorithms. Saved: {catalog_path}")
            return 0
        if args.run_job or args.resume:
            supplied = (args.run_job or args.resume).resolve()
            job = Job.load(supplied)
            if supplied != job_path(root, job).resolve():
                raise ValueError("Job must be in this checkout's jobs/<id>/job.json directory.")
            if args.resume and not job.remote_hash:
                raise ValueError("No saved remote hash to resume. Check MVSEP history before starting another upload.")
        else:
            metadata = {}
            if args.inspection:
                metadata = json.loads(args.inspection.read_text(encoding="utf-8"))
                if metadata.get("milestone") != "M1" or metadata.get("status") != "inspected":
                    raise ValueError("Expected a successful M1 inspection report.")
                source = Path(metadata["source"]["file_path"])
            else:
                source = args.file.resolve()
            validate_source(source)
            job = Job.create(source, settings.spec, metadata)
            job.save(job_path(root, job))
        if args.configure:
            from .core.active import record_worker
            from .core.lock import SingleJobLock
            from .core.setup import apply_choices
            from .setup_window import choose_settings
            from .separation import show_progress
            # Hold the existing process lock during setup too. A CLI invocation
            # must not edit a stale snapshot while another worker submits it.
            with SingleJobLock(root / "jobs" / ".active.lock"):
                job = Job.load(supplied)
                if job.remote_hash or job.status not in {JobStatus.CONFIGURING, JobStatus.PENDING}:
                    raise ValueError("Only a new, unsubmitted job can be configured.")
                job.status = JobStatus.CONFIGURING
                job.save(supplied)
                record_worker(root, job)
                try:
                    choices = choose_settings(root, job)
                    language.set(load_language(root))
                    if choices is None:
                        job.status = JobStatus.CANCELLED
                        job.save(supplied)
                        logger.info("Separation cancelled before upload: %s", job.id)
                        return 0
                    apply_choices(job, *choices)
                    job.save(supplied)
                except Exception as exc:
                    job.status, job.error = JobStatus.FAILED, client.redactor.clean(exc)
                    job.save(supplied)
                    raise
            show_progress(root, job_path(root, job))
        completed = execute_job(root, job, settings, logger, client)
        from .completion import finish_download
        finish_download(root, completed, logger, show_window=args.notify or args.configure)
        message = tr('Разделение завершено. Скачано файлов: {v0}\n\nПапка результата:\n{v1}\n\nВ окне состояния доступны кнопки «В Media Pool» и «На новые дорожки».', v0=len(completed.result_files), v1=output_directory(root, completed))
        print(message)
        return 0
    except Exception as exc:
        message = client.redactor.clean(exc) if client else str(exc)
        if logger:
            logger.exception("M2 failed")
        else:
            print(f"M2: {message}")
        if args.notify or args.monitor:
            shown = args.notify and not args.monitor and show_failed_job(root, job)
            if not shown:
                show_message(tr('M2: {v0}\n\nПодробности в logs/ и jobs/ проекта.', v0=error_text(message)),
                             error=True, title="HVPC Resolve-MVSEP — M2")
        return 1
    finally:
        if logger:
            close_logger(logger)
