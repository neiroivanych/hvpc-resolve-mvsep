"""Local actions after successful download; never convert an import failure to a download failure."""
from .core.job import JobStatus
from .core.errors import InspectionError
from .core.recent_settings import remember_success
from .core.runner import job_path
from .separation import show_progress
from .import_results import run_import


def finish_download(root, job, logger, *, show_window):
    if job.status != JobStatus.COMPLETED:
        return
    path = job_path(root, job)
    try:
        remember_success(root, job)
    except (InspectionError, OSError, ValueError, TypeError, KeyError):
        logger.warning('Could not save last successful settings; downloaded results remain available.', exc_info=True)
    automatic = job.post_import_target in {'media_pool', 'timeline'} and job.post_import_state == 'pending'
    if automatic:
        # Persist the claim before calling Resolve. A resumed completed job must
        # not silently recreate tracks the user subsequently deleted.
        job.post_import_state = 'running'
        try:
            job.save(path)
        except OSError:
            logger.exception('Could not claim automatic import; use the result window to import manually.')
            automatic = False
    if show_window:
        reveal(root, path, logger)
    if automatic:
        try:
            outcome = run_import(root, path, job.post_import_target)
            job.post_import_state = 'completed' if outcome == 0 else 'failed'
        except Exception:
            job.post_import_state = 'failed'
            logger.exception('Automatic Resolve import failed; download is still completed.')
        try:
            job.save(path)
        except OSError:
            logger.exception('Could not save automatic import status; Resolve import receipt is available.')
        if show_window:
            reveal(root, path, logger)


def reveal(root, path, logger):
    try:
        show_progress(root, path)  # restore an existing window or recreate a closed one
    except OSError:
        logger.exception('Could not open the result window; reopen it from the script menu.')
