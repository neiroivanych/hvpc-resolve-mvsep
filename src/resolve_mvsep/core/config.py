"""M1 configuration. Credentials are neither needed nor loaded in M1."""

from __future__ import annotations

import json
from dataclasses import dataclass, fields
from pathlib import Path

from .errors import InspectionError


@dataclass(frozen=True)
class Settings:
    selection_mode: str = "selected"
    track_type: str = "audio"
    track_index: int = 1
    item_index: int = 1
    show_result_dialog: bool = True
    action: str = "inspect"


def load_settings(root: Path) -> Settings:
    path = root / "settings.json"
    if not path.exists():
        return Settings()
    try:
        # utf-8-sig also accepts files saved by Windows editors with a BOM.
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise InspectionError("Cannot read settings.json. Use valid UTF-8 JSON.") from exc
    if not isinstance(data, dict):
        raise InspectionError("settings.json must contain a JSON object.")
    if set(data) - {field.name for field in fields(Settings)}:
        raise InspectionError("Unknown setting in settings.json. See settings.example.json.")
    settings = Settings(**data)
    if settings.action not in ("inspect", "separate"):
        raise InspectionError("action must be inspect (M1) or separate (M2).")
    if settings.selection_mode not in ("selected", "current_video", "track_item"):
        raise InspectionError("selection_mode must be selected, current_video or track_item.")
    if settings.track_type not in ("audio", "video"):
        raise InspectionError("track_type must be audio or video.")
    for value in (settings.track_index, settings.item_index):
        if type(value) is not int or value < 1:
            raise InspectionError("track_index and item_index must be positive integers (1-based).")
    if type(settings.show_result_dialog) is not bool:
        raise InspectionError("show_result_dialog must be true or false.")
    return settings
