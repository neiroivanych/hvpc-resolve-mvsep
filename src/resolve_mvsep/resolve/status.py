"""A small read-only SDK probe. Always invoked in an isolated, bounded process."""
from ..core.resolve_status import ResolveStatus
from .api import _load_script_module


def inspect_connection(job, application=None):
    saved = job.source_clip
    if not saved.get('project_name') or not saved.get('timeline_id'):
        return ResolveStatus('no_context')
    try:
        app = application if application is not None else _load_script_module().scriptapp('Resolve')
        if app is None:
            return ResolveStatus('unavailable')
        manager = app.GetProjectManager()
        if manager is None:
            return ResolveStatus('unavailable')
        project = manager.GetCurrentProject()
        if project is None:
            return ResolveStatus('no_project')
        if saved.get('project_id'):
            matches = project.GetUniqueId() == saved['project_id']
        else:
            matches = project.GetName() == saved['project_name'] and any(
                project.GetTimelineByIndex(i).GetUniqueId() == saved['timeline_id']
                for i in range(1, project.GetTimelineCount() + 1))
        if not matches:
            return ResolveStatus('wrong_project')
        timeline = project.GetCurrentTimeline()
        if timeline is None:
            return ResolveStatus('no_timeline')
        if timeline.GetUniqueId() != saved['timeline_id']:
            return ResolveStatus('wrong_timeline')
        version = tuple(int(v) for v in app.GetVersion()[:3])
        return ResolveStatus('ready' if version >= (21, 1, 0) else 'old_version')
    except Exception:
        # Startup/shutdown and the SDK bridge may fail between any two reads.
        return ResolveStatus('unavailable')
