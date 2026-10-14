"""Local named separation settings, never credentials or mutable job references."""

from __future__ import annotations

from .i18n import tr

import json
from pathlib import Path

from ..mvsep.models import Mode, SeparationSpec
from .errors import InspectionError
from .lock import SingleJobLock

FIELDS = {"mode", "sep_type", "preset_id", "api_options", "output_format"}


def spec_data(spec: SeparationSpec) -> dict:
    return {"mode": spec.mode.value, "sep_type": spec.sep_type, "preset_id": spec.preset_id,
            "api_options": dict(spec.api_options), "output_format": spec.output_format}


def read_spec(data: dict) -> SeparationSpec:
    if not isinstance(data, dict) or set(data) != FIELDS:
        raise ValueError("Invalid separation profile fields.")
    return SeparationSpec(Mode(data["mode"]), data["sep_type"], data["preset_id"],
                          dict(data["api_options"]), data["output_format"])


def load_profiles(root: Path) -> dict[str, SeparationSpec]:
    path = root / "config" / "profiles.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if set(data) != {"version", "profiles"} or data["version"] != 1 or not isinstance(data["profiles"], dict):
            raise ValueError()
        if any(not isinstance(name, str) or not name.strip() or len(name) > 80 for name in data["profiles"]):
            raise ValueError()
        return {name: read_spec(spec) for name, spec in data["profiles"].items()}
    except (OSError, ValueError, KeyError, TypeError):
        raise InspectionError(tr('Не удалось прочитать config/profiles.json. Файл не был перезаписан.')) from None


def save_profile(root: Path, name: str, spec: SeparationSpec) -> None:
    name = name.strip()
    if not name or len(name) > 80 or any(ord(char) < 32 for char in name):
        raise InspectionError(tr('Укажите название набора: от 1 до 80 символов.'))
    path = root / "config" / "profiles.json"
    with SingleJobLock(path.with_suffix(".lock")):
        profiles = load_profiles(root)
        if any(existing.casefold() == name.casefold() for existing in profiles):
            raise InspectionError(tr('Такое название уже есть. Укажите другое имя для нового набора.'))
        profiles[name] = read_spec(spec_data(spec))
        content = {"version": 1, "profiles": {key: spec_data(value) for key, value in profiles.items()}}
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(content, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)


def job_spec(job) -> SeparationSpec:
    """Use the saved recipe, never today's global settings or catalog defaults."""
    if job.settings_snapshot:
        return read_spec(job.settings_snapshot["specification"])
    return read_spec(spec_data(job.specification()))


def read_result_settings(path: Path) -> tuple[SeparationSpec, str]:
    """Read our portable report as data only; never follow its source/result paths."""
    try:
        if path.stat().st_size > 2 * 1024 * 1024:
            raise ValueError()
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if data.get("version") != 1 or not isinstance(data.get("settings"), dict):
            raise ValueError()
        spec = read_spec(data["settings"]["specification"])
        source = Path(data.get("source", {}).get("file", tr('Результат'))).stem
        number = data.get("run_index")
        label = f"{source} — {number:03d}" if type(number) is int and number > 0 else source
        return spec, label[:80]
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        raise InspectionError(tr('Выберите файл .settings.json, созданный HVPC Resolve-MVSEP. TXT предназначен для чтения, JSON — для загрузки настроек.')) from None
