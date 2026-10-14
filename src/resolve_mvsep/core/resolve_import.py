"""Local import receipts, independent of the completed MVSEP download job."""
import json
import os
from pathlib import Path

from .active import process_identity
from .i18n import tr
from .error_copy import error_text
from .job import Job, JobStatus, now


def receipt_path(job_path: Path) -> Path:
    return job_path.with_name('resolve-import.json')


def read_receipt(job_path: Path) -> dict:
    try:
        value = json.loads(receipt_path(job_path).read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def save_receipt(job_path: Path, data: dict) -> None:
    data['updated_at'] = now()
    path = receipt_path(job_path)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.replace(temporary, path)


def import_running(data: dict) -> bool:
    return (data.get('status') == 'running' and bool(data.get('identity'))
            and process_identity(data.get('pid')) == data['identity'])


def result_tracks(data: dict) -> list[int]:
    return sorted({item['track'] for item in data.get('timeline_items', {}).values()
                   if isinstance(item, dict) and type(item.get('track')) is int and item['track'] > 0})


def track_range(data: dict) -> str:
    """Keep gaps visible: A4, A6–A8 must never imply that A5 is an output."""
    tracks = result_tracks(data)
    spans = []
    for index in tracks:
        if spans and index == spans[-1][1] + 1:
            spans[-1][1] = index
        else:
            spans.append([index, index])
    return ', '.join(f'A{first}' if first == last else f'A{first}–A{last}' for first, last in spans)


def timeline_confirmed(data: dict) -> bool:
    return bool(data.get('status') == 'completed' and data.get('target') == 'timeline'
                and data.get('timeline_verified_at') and result_tracks(data))


def track_location(data: dict) -> str:
    return tr('Таймлайн «{name}» • {tracks}', name=data.get('timeline_name') or '—', tracks=track_range(data))


def track_scroll_hint(data: dict) -> str:
    tracks = result_tracks(data)
    return (tr('Прокрутите область аудиодорожек вниз до A{track}.', track=tracks[0]) if tracks else
            tr('Нажмите «На новые дорожки», чтобы проверить расположение.'))


def track_details(data: dict) -> str:
    if data.get('target') != 'timeline' or not data.get('timeline_items'):
        return ''
    lines = [track_location(data), track_scroll_hint(data)] if timeline_confirmed(data) else [
        tr('Ранее записанное расположение (требует проверки):'), track_location(data)]
    for key, item in sorted(data['timeline_items'].items(), key=lambda pair: pair[1].get('track', 0)):
        if type(item.get('track')) is int and item['track'] > 0:
            lines.append(f"A{item['track']} — {item.get('file_name') or Path(key).name}")
    return '\r\n'.join(lines)


def import_message(data: dict) -> str:
    if data.get('status') == 'running':
        return (tr('Добавляем результаты в Resolve…') if import_running(data) else
                tr('Импорт прерван. Можно повторить: уже добавленные файлы будут найдены.'))
    if data.get('status') == 'failed':
        return tr('Импорт не завершён: {error}', error=error_text(data.get('error', '')))
    if data.get('status') == 'completed':
        if data.get('target') == 'timeline':
            if not timeline_confirmed(data):
                return tr('Импорт записан ранее. Нажмите «На новые дорожки», чтобы проверить расположение.')
            return tr('Проверено при импорте — файлов: {count}; дорожки: {tracks}. Звук новых дорожек отключён (M).',
                      count=len(data.get('timeline_items', {})), tracks=track_range(data))
        return tr('Файлы в Media Pool: {count}. Можно добавить их на таймлайн.', count=len(data.get('media_items', {})))
    return tr('Добавьте файлы в Media Pool или разместите их на новых аудиодорожках.')


def latest_completed(root: Path, source_job: Job) -> Path | None:
    """Reopen an existing result without creating a new remote separation."""
    matches = []
    for path in (root / 'jobs').glob('*/job.json'):
        try:
            job = Job.load(path)
            if (job.status == JobStatus.COMPLETED and job.result_files
                    and Path(job.source_file).resolve() == Path(source_job.source_file).resolve()
                    and job.source_clip.get('timeline_id') == source_job.source_clip.get('timeline_id')):
                matches.append((job.created_at, path))
        except (OSError, ValueError, TypeError, KeyError):
            continue
    return max(matches, key=lambda item: item[0])[1] if matches else None


def latest_completed_for_project(root: Path, context: dict) -> Path | None:
    """A different selected source must not hide the project's last downloaded result."""
    matches = []
    for path in (root / 'jobs').glob('*/job.json'):
        try:
            job = Job.load(path)
            saved = job.source_clip
            if saved.get('project_id') and context.get('project_id'):
                same_project = saved['project_id'] == context['project_id']
            else:
                same_project = (bool(context.get('timeline_id')) and saved.get('timeline_id') == context['timeline_id']
                                and saved.get('project_name') == context.get('project_name'))
            if same_project and job.status == JobStatus.COMPLETED and job.result_files:
                matches.append((job.separation_completed_at or job.updated_at, path))
        except (OSError, ValueError, TypeError, KeyError):
            continue
    return max(matches, key=lambda item: item[0])[1] if matches else None
