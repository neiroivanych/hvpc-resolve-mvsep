"""Separate local MVSEP configuration; never required by M1 inspection."""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from ..mvsep.models import DEFAULT_ALGORITHMS, Mode, SeparationSpec
from .errors import InspectionError


@dataclass(frozen=True)
class MVSEPSettings:
    api_token: str = field(repr=False)
    spec: SeparationSpec
    request_timeout_seconds: float = 60
    job_timeout_seconds: float = 3600
    download_timeout_seconds: float = 900
    poll_interval_seconds: float = 10
    max_get_retries: int = 3


def load_mvsep_settings(root: Path, *, require_token: bool = True,
                        environ: Mapping[str, str] | None = None) -> MVSEPSettings:
    path = root / "config" / "mvsep.json"
    data = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            # JSONDecodeError/UnicodeDecodeError may include pieces of a secret.
            raise InspectionError("Cannot read config/mvsep.json. Check its UTF-8 JSON syntax.") from None
    allowed = {"api_token", "mode", "sep_type", "preset_id", "api_options", "output_format",
               "request_timeout_seconds", "job_timeout_seconds", "download_timeout_seconds",
               "poll_interval_seconds", "max_get_retries"}
    if not isinstance(data, dict) or set(data) - allowed:
        raise InspectionError("Invalid MVSEP settings. See mvsep.example.json; values are not logged.")
    environment = os.environ if environ is None else environ
    local_token = data.get("api_token", "")
    token = environment.get("MVSEP_API_TOKEN", "").strip()
    if not token:
        if not isinstance(local_token, str):
            raise InspectionError("api_token in config/mvsep.json must be a string.")
        token = local_token.strip()
    if require_token and not token:
        raise InspectionError(
            "MVSEP API token is missing. Set MVSEP_API_TOKEN or api_token in config/mvsep.json. "
            "Enter the token locally, not in chat."
        )
    try:
        mode = Mode(data.get("mode", Mode.VOCALS_INSTRUMENTAL.value))
        sep_type, preset = data.get("sep_type"), data.get("preset_id")
        if sep_type is None and preset is None:
            sep_type = DEFAULT_ALGORITHMS[mode]
        spec = SeparationSpec(mode, sep_type, preset, data.get("api_options", {}), data.get("output_format", 1))
    except (TypeError, ValueError):
        raise InspectionError("Invalid mode/algorithm/options/format in config/mvsep.json. See mvsep.example.json.") from None
    kwargs = {}
    for key, default in (("request_timeout_seconds", 60), ("job_timeout_seconds", 3600),
                         ("download_timeout_seconds", 900), ("poll_interval_seconds", 10)):
        value = data.get(key, default)
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise InspectionError("MVSEP intervals and timeouts must be finite positive numbers.")
        kwargs[key] = float(value)
    retries = data.get("max_get_retries", 3)
    if type(retries) is not int or not 0 <= retries <= 10:
        raise InspectionError("max_get_retries must be an integer from 0 to 10.")
    return MVSEPSettings(token, spec, max_get_retries=retries, **kwargs)
