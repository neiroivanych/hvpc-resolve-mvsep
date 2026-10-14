"""Durable attempt numbers, shared by all settings for identical source bytes."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .errors import InspectionError
from .job import Job
from .lock import SingleJobLock


def source_digest(source: Path) -> str:
    before = source.stat()
    digest = hashlib.sha256()
    with source.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    after = source.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise InspectionError("Исходный файл изменился во время проверки. Запустите разделение заново.")
    return digest.hexdigest()


def reserve_run(root: Path, job: Job) -> None:
    """Call before first upload. Ledger survives removal of job/output folders.

    Reserve by job ID before saving Job so a crash between the two writes never
    consumes a second number for the same task. No settings enter the source key.
    """
    if job.run_index is not None:
        return
    digest = job.source_sha256 or source_digest(Path(job.source_file))
    path = root / "config" / "source-runs.json"
    with SingleJobLock(path.with_suffix(".lock")):
        data = {"version": 1, "sources": {}}
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if (set(data) != {"version", "sources"} or data["version"] != 1
                        or not isinstance(data["sources"], dict)):
                    raise ValueError()
                for key, assignments in data["sources"].items():
                    if len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
                        raise ValueError()
                    if not isinstance(assignments, dict):
                        raise ValueError()
                    if any(len(identifier) != 32 or any(c not in "0123456789abcdef" for c in identifier)
                           or type(number) is not int or number < 1 for identifier, number in assignments.items()):
                        raise ValueError()
                    if len(set(assignments.values())) != len(assignments):
                        raise ValueError()
            except (OSError, ValueError, TypeError, KeyError):
                raise InspectionError("Не удалось прочитать историю номеров config/source-runs.json. "
                                      "Она сохранена без изменений; отправка не началась.") from None
        assignments = data["sources"].setdefault(digest, {})
        if job.id not in assignments:
            assignments[job.id] = max(assignments.values(), default=0) + 1
            temporary = path.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
            temporary.replace(path)
        job.source_sha256, job.run_index = digest, assignments[job.id]
