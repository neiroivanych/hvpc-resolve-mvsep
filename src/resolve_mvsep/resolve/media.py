"""Inspect the original media path, never the proxy or render cache."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core.errors import InspectionError
from ..core.models import SourceMedia

PROPERTY_KEYS = (
    "Type", "Format", "FPS", "Start TC", "Duration", "Audio Codec",
    "Sample Rate", "Audio Ch", "Proxy", "Proxy Media Path", "Online Status",
)


def inspect_source(clip: Any) -> tuple[SourceMedia, list[str]]:
    media = clip.GetMediaPoolItem()
    if media is None:
        raise InspectionError("This timeline item has no MediaPoolItem. Select ordinary file-backed audio/video.")
    properties = media.GetClipProperty()
    if not isinstance(properties, dict):
        raise InspectionError("Resolve could not read MediaPoolItem clip properties.")
    source = properties.get("File Path")
    if not isinstance(source, str) or not source.strip():
        raise InspectionError(
            "MediaPoolItem has no File Path. Compound/multicam/nested clips, "
            "titles and generators are not supported in M1. Select an ordinary source clip."
        )
    path = Path(source)
    warnings = []
    try:
        exists = path.is_absolute() and path.is_file()
    except OSError:
        exists = False
    if not exists:
        warnings.append("Source path is not an accessible absolute file: offline media, missing file or image sequence. Relink/check it in Resolve.")
    return SourceMedia(
        file_path=source,
        file_exists=exists,
        media_pool_name=media.GetName(),
        media_pool_id=media.GetUniqueId(),
        properties={key: str(properties[key]) for key in PROPERTY_KEYS if key in properties},
    ), warnings
