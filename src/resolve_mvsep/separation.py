"""Launch a background HTTP worker after inspecting the clip on Resolve's thread."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

from .core.errors import InspectionError
from .core.active import active_job, launch_lock, record_worker, worker_running
from .core.feedback import show_message
from .core.i18n import tr, using_language, load_language
from .core.job import Job, JobStatus
from .core.lock import SingleJobLock
from .core.models import InspectionReport
from .core.mvsep_config import load_mvsep_settings
from .core.runner import job_path, validate_source


def worker_python() -> Path:
    bundled = Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / (
        "Blackmagic Design/DaVinci Resolve/ResolvePython/ResolvePython.exe"
    )
    if bundled.is_file():
        return bundled
    current = Path(sys.executable)
    if current.name.lower() in {"python.exe", "python3.exe", "python", "python3"} and current.is_file():
        return current
    installed = shutil.which("python")
    if installed:
        return Path(installed)
    raise InspectionError("Cannot find a Python worker. Resolve 21.1+ includes ResolvePython; otherwise install Python 3.10+.")


def show_progress(root: Path, path: Path, *, notify_failure: bool = True) -> bool:
    # Each viewer has a per-job OS lock; duplicates only restore its window.
    try:
        (path.parent / "show-window").touch()
        if Job.load(path).status == JobStatus.CONFIGURING:
            # The worker owns the setup window and responds to the same restore flag.
            return True
        with (path.parent / "window.log").open("ab") as output:
            subprocess.Popen([str(worker_python()), str(root / "src" / "mvsep_cli.py"),
                              "--monitor", str(path)], cwd=str(root), stdin=subprocess.DEVNULL,
                             stdout=output, stderr=subprocess.STDOUT, close_fds=True,
                             creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        return True
    except (OSError, ValueError, TypeError, KeyError):
        if notify_failure:
            with using_language(load_language(root)):
                show_message(tr("Задача запущена, но окно состояния открыть не удалось.\nСостояние: {path}", path=path),
                             title="HVPC Resolve-MVSEP — M2")
        return False


def reopen_active(root: Path, *, notify: bool) -> Path | None:
    with launch_lock(root):
        path = active_job(root)
        if path and notify:
            show_progress(root, path)
        return path


def resume_job(root: Path, path: Path):
    """Explicit retry of an existing remote hash; this path cannot create/upload."""
    with launch_lock(root):
        job = Job.load(path)
        if path.resolve() != job_path(root, job).resolve() or not job.remote_hash or job.status in {
                JobStatus.COMPLETED, JobStatus.CANCELLED, JobStatus.CONFIGURING}:
            raise InspectionError(tr('Эту задачу нельзя продолжить. Проверьте её состояние.'))
        current = active_job(root)
        if current or worker_running(path):
            if current is not None and current.resolve() != path.resolve():
                raise InspectionError(tr('Другая задача ещё выполняется. Дождитесь её завершения.'))
            show_progress(root, path)
            return None
        with SingleJobLock(root / 'jobs/.active.lock'):
            pass
        with (path.parent / 'worker.log').open('ab') as output:
            process = subprocess.Popen([str(worker_python()), str(root / 'src/mvsep_cli.py'),
                                        '--resume', str(path.resolve()), '--notify'],
                                       cwd=str(root), stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                                       close_fds=True, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        record_worker(root, job, process.pid)
        show_progress(root, path)
        return process


def launch_separation(root: Path, report: InspectionReport, *, notify: bool) -> Path:
    with launch_lock(root):
        path = active_job(root)
        if path is None:
            # A legacy/CLI worker without a discoverable record is still protected.
            with SingleJobLock(root / "jobs" / ".active.lock"):
                pass
            path = _start_worker(root, report, notify=notify)
        if notify:
            show_progress(root, path)
        return path


def _start_worker(root: Path, report: InspectionReport, *, notify: bool) -> Path:
    # Validate credentials now so a missing token is visible immediately. The
    # worker reads the local token again; it never appears in command arguments.
    settings = load_mvsep_settings(root)
    source = Path(report.source.file_path)
    validate_source(source, report.source.properties)
    executable = worker_python()
    job = Job.create(source, settings.spec, asdict(report))
    if notify:
        job.status = JobStatus.CONFIGURING
    path = job_path(root, job)
    job.save(path)
    arguments = [str(executable), str(root / "src" / "mvsep_cli.py"), "--run-job", str(path)]
    if notify:
        arguments.extend(["--configure", "--notify"])
    try:
        with (path.parent / "worker.log").open("ab") as output:
            process = subprocess.Popen(arguments, cwd=str(root), stdin=subprocess.DEVNULL, stdout=output,
                                       stderr=subprocess.STDOUT, close_fds=True,
                                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        record_worker(root, job, process.pid)
    except OSError:
        raise InspectionError("Could not start the MVSEP worker. The pending job is saved for a later CLI launch.") from None
    return path
