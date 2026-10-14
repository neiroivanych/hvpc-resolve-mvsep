"""Two separate, credential-free snapshots: last choice and last successful job."""
import json
from pathlib import Path

from .i18n import load_preferences
from .errors import InspectionError
from .job import JobStatus, now
from .lock import SingleJobLock
from .profiles import job_spec, read_spec, spec_data


def policy(root: Path) -> tuple[bool, str]:
    prefs = load_preferences(root)
    mode = prefs.get('remember_settings_mode', 'successful')
    return prefs.get('remember_last_settings') is True, mode if mode in {'all', 'successful'} else 'successful'


def _load(root: Path) -> dict:
    path = root / 'config/recent_settings.json'
    if not path.exists():
        return {'version': 1}
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict) or data.get('version') != 1:
        raise ValueError('Invalid recent settings file.')
    return data


def remembered_spec(root: Path):
    enabled, mode = policy(root)
    if enabled:
        try:
            snapshot = _load(root).get(mode)
            if snapshot:
                return read_spec(snapshot['specification'])
        except (InspectionError, OSError, ValueError, TypeError, KeyError):
            pass
    return None


def _save(root: Path, slot: str, spec, *, saved_at: str, job_id=None):
    path = root / 'config/recent_settings.json'
    with SingleJobLock(path.with_suffix('.lock')):
        data = _load(root)
        # Resuming an older completed job must not displace a newer success.
        previous = data.get(slot, {})
        if not isinstance(previous, dict) or not isinstance(previous.get('saved_at', ''), str):
            raise ValueError('Invalid recent settings snapshot.')
        if previous.get('saved_at', '') > saved_at:
            return
        data[slot] = {'specification': spec_data(read_spec(spec_data(spec))), 'saved_at': saved_at}
        if job_id:
            data[slot]['job_id'] = job_id
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        temporary.replace(path)


def remember_choice(root: Path, spec):
    if policy(root)[0]:
        _save(root, 'all', spec, saved_at=now())


def remember_success(root: Path, job):
    if job.remember_settings and job.status == JobStatus.COMPLETED and job.result_files:
        _save(root, 'successful', job_spec(job), saved_at=job.separation_completed_at or job.updated_at, job_id=job.id)
