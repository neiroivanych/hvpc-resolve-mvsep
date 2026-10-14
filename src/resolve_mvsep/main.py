"""Inspect on Resolve's thread; optionally hand a frozen snapshot to the M2 worker."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .core.config import load_settings
from .core.feedback import show_message
from .core.i18n import language, load_language, tr
from .core.logging import close_logger, create_logger
from .core.models import InspectionReport
from .resolve.api import connect
from .resolve.media import inspect_source
from .resolve.timeline import NoClipSelected, choose_clip, inspect_clip, timeline_frame_rate


def run(project_root: Path, injected_resolve: Any = None) -> int:
    """Return 0 for inspection (possibly with warnings), 1 for failure."""
    language.set(load_language(project_root))
    # The menu launcher supplies Resolve's injected application object. Keep
    # terminal invocations non-interactive, and show menu results independently
    # of whether Resolve routes this interpreter's stdout into Console.
    show_dialog = injected_resolve is not None
    stage = "M1"
    try:
        logger, log_path = create_logger(project_root)
    except OSError as exc:
        message = f"HVPC Resolve-MVSEP M1: cannot create logs in {project_root / 'logs'}: {exc}"
        print(message)
        if show_dialog:
            show_message(message, error=True)
        return 1
    try:
        logger.info("HVPC Resolve-MVSEP M1 started (read-only inspection). Log: %s", log_path)
        settings = load_settings(project_root)
        stage = "M2" if settings.action == "separate" else "M1"
        show_dialog = show_dialog and settings.show_result_dialog
        if settings.action == "separate":
            from .separation import reopen_active
            if reopen_active(project_root, notify=show_dialog):
                logger.info("Showing the active M2 job; no upload repeated.")
                return 0
        context = connect(logger, injected_resolve)
        try:
            clip = choose_clip(context, settings, logger)
        except NoClipSelected:
            if settings.action == 'separate' and show_dialog:
                from .core.resolve_import import latest_completed_for_project
                from .separation import show_progress
                last = latest_completed_for_project(project_root, {
                    'project_id': context.project.GetUniqueId(), 'project_name': context.project.GetName(),
                    'timeline_id': context.timeline.GetUniqueId()})
                if last:
                    show_progress(project_root, last)
                    logger.info('No selected clip; reopened the last completed result for this project.')
                    return 0
            raise
        metadata = inspect_clip(clip, context.timeline, settings.selection_mode,
                                source_times=context.version >= (21, 1, 0))
        logger.info("Clip: %s | %s %d (%s) | start=%s end=%s duration=%s frames",
                    metadata.name, metadata.track_type, metadata.track_index, metadata.track_name,
                    metadata.timeline_start, metadata.timeline_end, metadata.duration_frames)
        source, warnings = inspect_source(clip)
        logger.info("Source path: %s | file exists: %s", source.file_path, source.file_exists)
        report = InspectionReport(
            inspected_at=datetime.now(timezone.utc).isoformat(),
            resolve_version=context.application.GetVersionString(),
            project_name=context.project.GetName(),
            timeline_name=context.timeline.GetName(),
            timeline_id=context.timeline.GetUniqueId(),
            timeline_start_frame=context.timeline.GetStartFrame(),
            timeline_start_timecode=context.timeline.GetStartTimecode(),
            timeline_frame_rate=timeline_frame_rate(context),
            playhead_timecode=context.timeline.GetCurrentTimecode(),
            clip=metadata,
            source=source,
            warnings=warnings,
            project_id=context.project.GetUniqueId(),
        )
        for warning in warnings:
            logger.warning(warning)
        report_path = log_path.with_suffix(".json")
        rendered = json.dumps(asdict(report), ensure_ascii=False, indent=2)
        report_path.write_text(rendered + "\n", encoding="utf-8")
        logger.info("Inspection report:\n%s", rendered)
        logger.info("M1 inspection complete. JSON: %s", report_path)
        if settings.action == "separate":
            from .separation import launch_separation

            job_path = launch_separation(project_root, report, notify=show_dialog)
            logger.info("M2 worker started. Job state: %s", job_path)
            return 0
        logger.info("No media uploaded, imported or modified. M2 has not been started.")
        if show_dialog:
            summary = (
                "Проверка M1 завершена" + (" с предупреждением.\n\n" if warnings else ".\n\n")
                + f"Проект: {report.project_name}\n"
                + f"Timeline: {report.timeline_name}\n"
                + f"Клип: {metadata.name}\n"
                + f"Дорожка: {metadata.track_name}\n\n"
                + f"Исходный файл:\n{source.file_path}\n"
                + f"Файл найден на диске: {'да' if source.file_exists else 'нет'}\n\n"
                + ("Предупреждения:\n" + "\n".join(warnings) + "\n\n" if warnings else "")
                + f"Подробный отчёт:\n{report_path}\n\n"
                + "Это проверка клипа. Разделение аудио ещё не выполнялось."
            )
            if not show_message(summary):
                logger.warning("Could not display the result dialog. The inspection report is saved.")
        return 0
    except Exception as exc:
        logger.exception("HVPC Resolve-MVSEP failed. See the message and traceback below.")
        if show_dialog:
            message = tr("{stage}: выполнение не завершено.\n\n{error}\n\nПодробности ошибки:\n{path}",
                         stage=stage, error=exc, path=log_path)
            if not show_message(message, error=True, title=f"HVPC Resolve-MVSEP — {stage}"):
                logger.warning("Could not display the error dialog. See the log file.")
        return 1
    finally:
        close_logger(logger)
