"""Connect to Resolve without shipping or modifying Blackmagic's SDK."""

from __future__ import annotations

import importlib
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..core.errors import InspectionError


@dataclass(frozen=True)
class ResolveContext:
    application: Any
    project_manager: Any
    project: Any
    timeline: Any
    version: tuple[int, int, int]


def _load_script_module() -> Any:
    try:
        return importlib.import_module("DaVinciResolveScript")
    except ModuleNotFoundError as exc:
        if exc.name != "DaVinciResolveScript":
            raise
    api_root = os.environ.get("RESOLVE_SCRIPT_API")
    if api_root:
        modules = Path(api_root) / "Modules"
    elif sys.platform == "win32":
        modules = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / (
            "Blackmagic Design/DaVinci Resolve/Support/Developer/Scripting/Modules"
        )
    else:
        raise InspectionError(
            "DaVinciResolveScript is unavailable. M1 targets Windows; "
            "set RESOLVE_SCRIPT_API to your installed Developer/Scripting folder."
        )
    if not (modules / "DaVinciResolveScript.py").is_file():
        raise InspectionError(
            "Resolve scripting module not found. Check the Resolve installation "
            "or RESOLVE_SCRIPT_API (Developer/Scripting, without /Modules)."
        )
    sys.path.insert(0, str(modules))
    try:
        return importlib.import_module("DaVinciResolveScript")
    finally:
        sys.path.remove(str(modules))


def connect(logger: logging.Logger, injected_resolve: Any = None) -> ResolveContext:
    application = injected_resolve
    if application is None:
        try:
            application = _load_script_module().scriptapp("Resolve")
        except (ImportError, OSError) as exc:
            raise InspectionError(
                "Cannot load Resolve's native scripting bridge. Use the built-in "
                "ResolvePython on Resolve 21.1+, or a compatible 64-bit Python. "
                "For a custom installation, check RESOLVE_SCRIPT_LIB."
            ) from exc
    if application is None:
        logger.error("Resolve detected: no")
        raise InspectionError(
            "Resolve is not available. Open Resolve Studio and wait for it to load. "
            "For a terminal launch, set Preferences > System > General > "
            "External scripting to Local, then restart Resolve if needed."
        )
    logger.info("Resolve detected: yes (%s)", application.GetVersionString())
    raw_version = application.GetVersion()
    if not isinstance(raw_version, (list, tuple)) or len(raw_version) < 3:
        raise InspectionError("Resolve returned an invalid product version.")
    version = tuple(int(part) for part in raw_version[:3])
    manager = application.GetProjectManager()
    if manager is None:
        raise InspectionError("Project Manager is unavailable. Wait for Resolve to finish loading.")
    project = manager.GetCurrentProject()
    if project is None:
        raise InspectionError("No current project. Open a project in Resolve.")
    logger.info("Project: %s", project.GetName())
    timeline = project.GetCurrentTimeline()
    if timeline is None:
        raise InspectionError("No current timeline. Open a timeline in the Edit page.")
    logger.info("Timeline: %s", timeline.GetName())
    return ResolveContext(application, manager, project, timeline, version)
