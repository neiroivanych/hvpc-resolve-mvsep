"""Serializable inspection metadata, independent of native Resolve objects."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SourceMedia:
    file_path: str
    file_exists: bool
    media_pool_name: str
    media_pool_id: str
    properties: dict[str, str]


@dataclass(frozen=True)
class ClipMetadata:
    name: str
    unique_id: str
    selection_method: str
    track_type: str
    track_index: int
    track_name: str
    timeline_start: float
    timeline_end: float
    duration_frames: float
    offset_from_timeline_start: float
    left_available_frames: float
    right_available_frames: float
    source_start_seconds: float | None = None
    source_end_seconds: float | None = None


@dataclass(frozen=True)
class InspectionReport:
    inspected_at: str
    resolve_version: str
    project_name: str
    timeline_name: str
    timeline_id: str
    timeline_start_frame: int
    timeline_start_timecode: str
    timeline_frame_rate: str
    playhead_timecode: str
    clip: ClipMetadata
    source: SourceMedia
    warnings: list[str] = field(default_factory=list)
    milestone: str = "M1"
    status: str = "inspected"
    project_id: str | None = None
