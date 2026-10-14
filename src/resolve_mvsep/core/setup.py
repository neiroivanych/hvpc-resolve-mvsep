"""Per-separation choices, independent of Windows controls and API credentials."""

from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path

from ..mvsep.models import SeparationSpec
from .errors import InspectionError
from .job import Job, JobStatus
from .profiles import read_spec, spec_data

FORMAT_NAMES = {1: "WAV — 16 бит, без сжатия", 0: "MP3 — 320 кбит/с",
                2: "FLAC — 16 бит", 3: "M4A", 4: "WAV — 32 бита", 5: "FLAC — 24 бита"}


def cached_algorithms(root: Path) -> dict[int, dict]:
    try:
        data = json.loads((root / "logs" / "mvsep_algorithms.json").read_text(encoding="utf-8"))
        return {item["render_id"]: item for item in data if isinstance(item, dict) and type(item.get("render_id")) is int}
    except (OSError, ValueError, TypeError):
        return {}


def freeze_defaults(spec: SeparationSpec, catalog: dict[int, dict]) -> SeparationSpec:
    """Pin known unconditional model defaults when saving a reusable local profile."""
    options = dict(spec.api_options)
    if spec.preset_id is None:
        for field in catalog.get(spec.sep_type, {}).get("algorithm_fields", []):
            if not isinstance(field, dict):
                continue
            name, value = field.get("name"), field.get("default_key")
            if (isinstance(name, str) and re.fullmatch(r"add_opt[1-9][0-9]*", name)
                    and value is not None and not field.get("conditions")):
                options.setdefault(name, str(value))
    return replace(spec, api_options=options)


def with_format(spec: SeparationSpec, output_format: int) -> SeparationSpec:
    if spec.preset_id is not None and output_format != spec.output_format:
        raise InspectionError("Формат серверного пресета задаётся в самом пресете MVSEP.")
    return replace(spec, api_options=dict(spec.api_options), output_format=output_format)


def apply_choices(job: Job, spec: SeparationSpec, profile_name: str | None) -> None:
    if job.remote_hash or job.status not in {JobStatus.CONFIGURING, JobStatus.PENDING}:
        raise InspectionError("Настройки уже отправленной задачи менять нельзя. Создайте новое разделение.")
    snapshot = read_spec(spec_data(spec))
    job.mode, job.sep_type, job.preset_id = snapshot.mode, snapshot.sep_type, snapshot.preset_id
    job.api_options, job.output_format = dict(snapshot.api_options), snapshot.output_format
    job.settings_profile = profile_name
    job.status = JobStatus.PENDING
