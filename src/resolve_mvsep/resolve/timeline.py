"""Explicit clip selection; no selection, playhead or track mutations."""

from __future__ import annotations

import logging
import os
from typing import Any

from ..core.config import Settings
from ..core.errors import InspectionError
from ..core.i18n import tr
from ..core.models import ClipMetadata
from .api import ResolveContext


class NoClipSelected(InspectionError):
    """An empty selection, distinguished from unsupported/failed selection reads."""


def linked_audio_selection(clips):
    """Only collapse one verified video/audio pair backed by the same media.

    Linked Selection normally selects both. Unrelated selections, linked external
    sound, and multiple audio components must never silently choose a source.
    """
    if len(clips) != 2:
        return None
    try:
        by_type = {item.GetTrackTypeAndIndex()[0]: item for item in clips}
        if set(by_type) != {'audio', 'video'}:
            return None
        audio, video = by_type['audio'], by_type['video']
        am, vm = audio.GetMediaPoolItem(), video.GetMediaPoolItem()
        if not am or not vm or not am.GetUniqueId() or am.GetUniqueId() != vm.GetUniqueId():
            return None
        ap, vp = am.GetClipProperty('File Path'), vm.GetClipProperty('File Path')
        if not ap or not vp or os.path.normcase(os.path.abspath(ap)) != os.path.normcase(os.path.abspath(vp)):
            return None
        for item, partner in ((audio, video), (video, audio)):
            linked = item.GetLinkedItems()
            if not isinstance(linked, (list, tuple)) or partner.GetUniqueId() not in {
                    entry.GetUniqueId() for entry in linked}:
                return None
        return audio
    except (AttributeError, TypeError, ValueError, RuntimeError):
        return None


def choose_clip(context: ResolveContext, settings: Settings, logger: logging.Logger) -> Any:
    timeline = context.timeline
    logger.info("Selection mode: %s", settings.selection_mode)
    if settings.selection_mode == "selected":
        # Native proxy objects may return callable placeholders for unknown names.
        # The SDK changelog explicitly introduces this API in 21.0.4.
        if context.version < (21, 0, 4):
            raise InspectionError(
                "Timeline.GetSelectedClips requires Resolve 21.0.4+. "
                "Use selection_mode=current_video or track_item in settings.json "
                "as an explicit fallback. See README."
            )
        clips = timeline.GetSelectedClips()
        if clips is None:
            raise InspectionError("Resolve could not read timeline selection. Select one clip in Edit.")
        if not isinstance(clips, (list, tuple)):
            raise InspectionError("Resolve returned an unexpected timeline selection format.")
        if not clips:
            raise NoClipSelected("No timeline clip selected. Select one clip on the timeline, not in Media Pool.")
        if len(clips) != 1:
            logger.info("Selected timeline item count: %d", len(clips))
            audio = linked_audio_selection(clips)
            if audio is not None:
                logger.info("One linked video/audio pair selected; using its audio component as the placement anchor.")
                return audio
            raise InspectionError(
                tr('Выбрано клипов: {count}. Выберите один клип или связанную пару «видео + его звук» из одного файла. '
                   'Чтобы выбрать отдельный компонент, используйте Alt + щелчок.', count=len(clips))
            )
        return clips[0]
    if settings.selection_mode == "current_video":
        logger.warning("Explicit fallback: current video item, not the highlighted selection.")
        clip = timeline.GetCurrentVideoItem()
        if clip is None:
            raise InspectionError("No current video item. Move the playhead onto video or use track_item for audio.")
        return clip
    count = timeline.GetTrackCount(settings.track_type)
    if not isinstance(count, int) or settings.track_index > count:
        raise InspectionError("Configured track does not exist. Check track_type and track_index.")
    clips = timeline.GetItemListInTrack(settings.track_type, settings.track_index) or []
    if settings.item_index > len(clips):
        raise InspectionError("Configured item does not exist on this track. Check item_index (1-based API list order).")
    logger.warning("Explicit fallback: track %s %d, item %d (API list order).",
                   settings.track_type, settings.track_index, settings.item_index)
    return clips[settings.item_index - 1]


def inspect_clip(clip: Any, timeline: Any, selection_method: str, *, source_times: bool = False) -> ClipMetadata:
    track = clip.GetTrackTypeAndIndex()
    if not isinstance(track, (list, tuple)) or len(track) != 2:
        raise InspectionError("Cannot determine clip track type and index.")
    track_type, track_index = str(track[0]), int(track[1])
    if track_type not in ("video", "audio"):
        raise InspectionError("Select an audio or video clip, not a subtitle.")
    start = float(clip.GetStart(True))
    return ClipMetadata(
        name=clip.GetName(),
        unique_id=clip.GetUniqueId(),
        selection_method=selection_method,
        track_type=track_type,
        track_index=track_index,
        track_name=timeline.GetTrackName(track_type, track_index),
        timeline_start=start,
        timeline_end=float(clip.GetEnd(True)),
        duration_frames=float(clip.GetDuration(True)),
        offset_from_timeline_start=start - timeline.GetStartFrame(),
        # These are available handles, NOT a guaranteed source in/out range.
        left_available_frames=float(clip.GetLeftOffset(True)),
        right_available_frames=float(clip.GetRightOffset(True)),
        source_start_seconds=float(clip.GetSourceStartTime()) if source_times else None,
        source_end_seconds=float(clip.GetSourceEndTime()) if source_times else None,
    )


def timeline_frame_rate(context: ResolveContext) -> str:
    if context.version >= (21, 1, 0):
        return str(context.timeline.GetSettings().get("timelineFrameRate", "unknown"))
    # Documented legacy API, deprecated in 21.1 (SDK README).
    return str(context.timeline.GetSetting("timelineFrameRate"))
