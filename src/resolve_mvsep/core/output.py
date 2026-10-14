"""Human-readable output locations; persisted paths keep resumes stable."""

from __future__ import annotations

import hashlib
import os
import tempfile
from datetime import datetime
from pathlib import Path, PurePosixPath

from ..mvsep.names import safe_name
from .errors import InspectionError
from .job import Job
from .i18n import load_preferences, tr


def default_output_root(root: Path) -> Path:
    value = load_preferences(root).get("output_root")
    return Path(value).resolve() if isinstance(value, str) and Path(value).is_absolute() else (root / "output").resolve()


def output_base(root: Path, job: Job) -> Path:
    # Legacy manifests always retain checkout/output, regardless of new preferences.
    if job.output_root is None:
        return (root / "output").resolve()
    if not isinstance(job.output_root, str) or not Path(job.output_root).is_absolute():
        raise InspectionError(tr("Выберите абсолютный путь папки результата."))
    return Path(job.output_root).resolve()


def prepare_output_root(base: Path) -> None:
    """Fail before upload if the chosen destination cannot accept results."""
    if not base.is_absolute():
        raise InspectionError(tr("Выберите абсолютный путь папки результата."))
    try:
        base.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=base) as stream:
            stream.write(b"HVPC Resolve-MVSEP destination check")
            stream.flush()
    except OSError as exc:
        raise InspectionError(tr("Папка недоступна для записи: {path}. Выберите другую папку.", path=base)) from exc


def output_directory(root: Path, job: Job) -> Path:
    base = output_base(root, job)
    if job.output_relative is None:
        return base / job.id  # old jobs keep their existing locations
    relative = PurePosixPath(job.output_relative)
    if relative.is_absolute() or any(p in {"..", "."} or ":" in p or "\\" in p for p in relative.parts):
        raise InspectionError(tr("Недопустимый путь папки результата."))
    target = (base / Path(*relative.parts)).resolve()
    if target == base or not target.is_relative_to(base):
        raise InspectionError(tr("Папка результата должна находиться внутри выбранной папки."))
    return target


def assign_output_directory(root: Path, job: Job) -> Path:
    if job.output_relative is not None:
        return output_directory(root, job)
    legacy = output_directory(root, job)
    if legacy.exists() or job.remote_hash or job.result_files:
        return legacy
    # Reuse a source folder across renamed/relocated byte-identical copies.
    group = None
    groups = {}
    base = output_base(root, job)
    for manifest in sorted((root / "jobs").glob("*/job.json")):
        try:
            previous = Job.load(manifest)
            if previous.id != job.id and previous.output_relative and output_base(root, previous) == base:
                output_directory(root, previous)  # validate before inheriting any components
                existing_group = PurePosixPath(previous.output_relative).parts[0]
                groups[existing_group.casefold()] = previous.source_sha256
                if job.source_sha256 and previous.source_sha256 == job.source_sha256:
                    group = existing_group
        except (OSError, ValueError, TypeError, KeyError, InspectionError):
            continue
    if group is None:
        source_name = safe_name(Path(job.source_file).stem)[:64].rstrip(" .")
        identity = job.source_sha256 or hashlib.sha256(os.path.normcase(job.source_file).encode()).hexdigest()
        group = source_name
        # Add a distinguishing suffix only for different sources with the same
        # visible name, or an existing directory with no recorded owner.
        for length in range(8, 65, 4):
            owner = groups.get(group.casefold())
            if owner == identity or (owner is None and group.casefold() not in groups
                                     and not (base / group).exists()):
                break
            group = f"{source_name} [{identity[:length]}]"
    local = datetime.fromisoformat(job.created_at).astimezone()
    variant = f"вариант {job.run_index:03d}" if job.run_index is not None else "запуск"
    # The per-source attempt number disambiguates submissions in the same minute.
    attempt = f"{local:%Y%m%d_%H%M} — {variant}"
    job.output_relative = f"{group}/{attempt}"
    return output_directory(root, job)
