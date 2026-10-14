"""Explicit, repeatable result import. Never uploads or changes original clips.

Resolve API calls run in a dedicated process. Each completed API operation is
recorded separately from job.json; actual Resolve contents are checked on retry.
"""
from __future__ import annotations

import logging
import math
import os
from pathlib import Path

from ..core.active import process_identity
from ..core.errors import InspectionError
from ..core.error_copy import error_text
from ..core.i18n import tr
from ..core.job import Job, JobStatus, now
from ..core.history import source_digest
from ..core.lock import SingleJobLock
from ..core.resolve_import import read_receipt, save_receipt
from .api import connect
from .timeline import timeline_frame_rate


def require(value, message):
    if not value:
        raise InspectionError(message)


def number(value, message):
    """Reject missing/non-numeric API values with a useful import diagnostic."""
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise InspectionError(message) from None
    require(not isinstance(value, bool) and math.isfinite(result), message)
    return result


def import_error(exc, operation):
    if isinstance(exc, InspectionError):
        if str(exc) == 'Another MVSEP job is already running. Wait for it to finish.':
            return tr('Другой импорт в Resolve уже выполняется. Дождитесь его завершения и повторите добавление.')
        return error_text(str(exc))
    return tr('Не удалось выполнить действие «{operation}». Ошибка: {reason}. Подробности: import-worker.log в папке задачи.',
              operation=operation, reason=str(exc) or type(exc).__name__)


def path_key(value) -> str:
    return os.path.normcase(str(Path(value).resolve())) if value else ''


def pool_items(folder):
    yield from folder.GetClipList() or []
    for child in folder.GetSubFolderList() or []:
        yield from pool_items(child)


def track_items(timeline, kind):
    for index in range(1, timeline.GetTrackCount(kind) + 1):
        for item in timeline.GetItemListInTrack(kind, index) or []:
            yield index, item


def audio_items(timeline):
    yield from track_items(timeline, 'audio')


def media_path(item):
    return path_key(item.GetClipProperty('File Path'))


def matching_audio(timeline, paths):
    found = {}
    for index, item in audio_items(timeline):
        media = item.GetMediaPoolItem()
        key = media_path(media) if media else ''
        if key in paths and key not in found:
            found[key] = (index, item)
    return found


def ensure_current(ctx, timeline=False):
    project = ctx.project_manager.GetCurrentProject()
    require(project and project.GetUniqueId() == ctx.project.GetUniqueId(),
            tr('Проект Resolve изменился во время импорта. Откройте исходный проект и повторите.'))
    if timeline:
        current = project.GetCurrentTimeline()
        require(current and current.GetUniqueId() == ctx.timeline.GetUniqueId(),
                tr('Откройте исходный таймлайн этой задачи и повторите импорт.'))


def validate_project(ctx, job):
    report = job.source_clip
    require(report.get('timeline_id') and report.get('project_name'),
            tr('У задачи нет привязки к проекту Resolve. Добавьте скачанные файлы вручную.'))
    if report.get('project_id'):
        matches = ctx.project.GetUniqueId() == report['project_id']
    else:
        # Older jobs have no project ID; verify both name AND a timeline UUID.
        matches = (ctx.project.GetName() == report['project_name'] and any(
            ctx.project.GetTimelineByIndex(i).GetUniqueId() == report['timeline_id']
            for i in range(1, ctx.project.GetTimelineCount() + 1)))
    require(matches, tr('Открыт другой проект Resolve. Откройте проект, из которого запускалось разделение: {name}', name=report['project_name']))


def placement(ctx, job, *, verify_file=True):
    """Map the same source's current in/out to its current timeline position.

    Stable clip/media identities and source bytes bind the job to its source;
    saved timeline coordinates do not. Movement and edge edits are permitted.
    """
    report, saved = job.source_clip, job.source_clip.get('clip', {})
    require(ctx.version >= (21, 1, 0), tr('Размещение на дорожках требует Resolve 21.1+. Импорт в Media Pool доступен отдельно.'))
    require(ctx.timeline.GetUniqueId() == report.get('timeline_id'),
            tr('Открыт другой таймлайн. Откройте исходный таймлайн этой задачи и повторите импорт.'))
    kind = saved.get('track_type')
    require(kind in {'audio', 'video'} and saved.get('unique_id'),
            tr('В задаче нет идентификатора исходного клипа. Используйте импорт в Media Pool.'))
    clips = [item for _, item in track_items(ctx.timeline, kind) if item.GetUniqueId() == saved.get('unique_id')]
    require(clips,
            tr('Исходный клип не найден на исходном таймлайне: он удалён или заменён. Используйте импорт в Media Pool.'))
    require(len(clips) == 1,
            tr('Resolve вернул несколько клипов с идентификатором исходника. Невозможно однозначно выбрать место добавления. Используйте импорт в Media Pool.'))
    clip = clips[0]
    media = clip.GetMediaPoolItem()
    require(media, tr('Клип потерял связь с исходным файлом в Media Pool. Восстановите связь или добавьте результаты вручную.'))
    require(media_path(media) == path_key(job.source_file)
            and media.GetUniqueId() == report.get('source', {}).get('media_pool_id'),
            tr('Исходник клипа заменён или перепривязан к другому файлу после запуска разделения. Используйте импорт в Media Pool.'))
    fps = number(timeline_frame_rate(ctx), tr('Resolve не сообщил корректную частоту кадров таймлайна. Повторите импорт.'))
    saved_fps = number(report.get('timeline_frame_rate'), tr('В задаче не сохранена частота кадров таймлайна. Используйте импорт в Media Pool.'))
    require(fps > 0 and math.isclose(fps, saved_fps, abs_tol=1e-6),
            tr('Частота кадров таймлайна изменилась. Используйте импорт в Media Pool.'))
    speed = clip.GetSpeed() or {}
    percentage = number(speed.get('Percentage') if isinstance(speed, dict) else None,
                        tr('Resolve не сообщил скорость исходного клипа. Без проверки скорости размещение невозможно. Используйте импорт в Media Pool.'))
    require(percentage == 100,
            tr('Скорость исходного клипа отличается от 100%: {speed}%. Автоматическое размещение не поддерживает изменение скорости. Используйте импорт в Media Pool.', speed=f'{percentage:g}'))
    range_error = tr('Resolve не сообщил корректные границы исходного клипа. Повторите импорт или используйте Media Pool.')
    start, end = number(clip.GetSourceStartTime(), range_error), number(clip.GetSourceEndTime(), range_error)
    duration = number(clip.GetDuration(True), range_error)
    record, record_end = number(clip.GetStart(True), range_error), number(clip.GetEnd(True), range_error)
    require(start >= 0 and end > start and duration > 0, range_error)
    require(math.isclose(record_end - record, duration, abs_tol=.01, rel_tol=0),
            tr('Границы исходного клипа изменились во время проверки. Дождитесь завершения монтажа и повторите добавление.'))
    require(math.isclose((end - start) * fps, duration, abs_tol=.01, rel_tol=0),
            tr('Длительность исходного клипа не соответствует участку исходного файла. Возможны реверс, стоп-кадр или переменная скорость. Используйте импорт в Media Pool.'))
    if verify_file:
        source = Path(job.source_file)
        try:
            # Check availability even for older jobs without a saved digest.
            with source.open('rb'):
                pass
            digest = source_digest(source) if job.source_sha256 else None
        except FileNotFoundError:
            raise InspectionError(tr('Исходный файл недоступен на диске (удалён или перемещён): {path}. Восстановите файл или используйте импорт в Media Pool.', path=job.source_file)) from None
        except PermissionError:
            raise InspectionError(tr('Нет доступа к исходному файлу: {path}. Проверьте права доступа или используйте импорт в Media Pool.', path=job.source_file)) from None
        except OSError as exc:
            raise InspectionError(tr('Не удалось прочитать исходный файл: {path}. Причина: {reason}. Используйте импорт в Media Pool.', path=job.source_file, reason=str(exc))) from exc
        except InspectionError as exc:
            raise InspectionError(tr('Исходный файл меняется на диске во время проверки. Дождитесь завершения записи файла и повторите импорт.')) from exc
        require(not job.source_sha256 or digest == job.source_sha256,
                tr('Содержимое исходного файла изменилось после разделения. Результаты относятся к предыдущему содержимому. Используйте импорт в Media Pool.'))
    return start, end, record, fps


def subfolder(pool, parent, name):
    found = next((folder for folder in parent.GetSubFolderList() or [] if folder.GetName() == name), None)
    result = found or pool.AddSubFolder(parent, name)
    require(result, tr('Resolve не создал папку в Media Pool.'))
    return result


def placement_matches(item, expected):
    return (math.isclose(float(item.GetStart(True)), expected['start'], abs_tol=.01, rel_tol=0)
            and math.isclose(float(item.GetDuration(True)), expected['duration'], abs_tol=.01, rel_tol=0)
            and math.isclose(float(item.GetSourceStartTime()), expected['source_start'], abs_tol=.0001, rel_tol=0))


def verify_timeline_results(ctx, files, expected_ids=None):
    """Read back actual track contents, not just AppendToTimeline return proxies."""
    ensure_current(ctx, True)
    observed = matching_audio(ctx.timeline, files)
    verified = {}
    for key, file_path in files.items():
        require(key in observed,
                tr('Файл не найден на таймлайне после импорта: {name}. Повторите добавление.', name=Path(file_path).name))
        index, item = observed[key]
        require(list(item.GetTrackTypeAndIndex()) == ['audio', index]
                and (not expected_ids or key not in expected_ids or item.GetUniqueId() == expected_ids[key]),
                tr('Не удалось подтвердить дорожку результата: {name}. Повторите проверку.', name=Path(file_path).name))
        start, end = float(item.GetStart(True)), float(item.GetEnd(True))
        require(math.isfinite(start) and math.isfinite(end) and end > start,
                tr('Не удалось подтвердить дорожку результата: {name}. Повторите проверку.', name=Path(file_path).name))
        verified[key] = {'id': item.GetUniqueId(), 'track': index,
                         'track_name': ctx.timeline.GetTrackName('audio', index),
                         'file_name': Path(file_path).name, 'start_frame': start, 'end_frame': end}
    ensure_current(ctx, True)
    return verified


def import_results(root: Path, path: Path, target: str, *, application=None, logger=None) -> dict:
    require(target in {'media_pool', 'timeline'}, 'Invalid import target.')
    job = Job.load(path)
    require(path.resolve() == (root / 'jobs' / job.id / 'job.json').resolve(), 'Job must belong to this checkout.')
    # Global lock prevents concurrent importers from changing folder/track state.
    with SingleJobLock(root / 'jobs' / '.resolve-import.lock'):
        data = read_receipt(path)
        data.update(status='running', target=target, error=None, pid=os.getpid(), identity=process_identity(os.getpid()),
                    operation=tr('Проверка готовности результатов'))
        if target == 'timeline':
            data.pop('timeline_verified_at', None)
        data.setdefault('media_items', {})
        data.setdefault('timeline_items', {})
        data.setdefault('reserved_tracks', {})
        save_receipt(path, data)
        try:
            require(job.status == JobStatus.COMPLETED and job.result_files, tr('Дождитесь завершения разделения.'))
            _import(root, path, job, target, data, application, logger or logging.getLogger(__name__))
            data.update(status='completed', operation=None)
            save_receipt(path, data)
            return data
        except Exception as exc:
            data.update(status='failed', error=import_error(exc, data['operation']))
            save_receipt(path, data)
            raise


def _import(root, path, job, target, data, application, logger):
    files = {path_key(value): str(Path(value).resolve()) for value in job.result_files}
    paths = list(files)
    for value in paths:
        require(Path(value).is_file(), tr('Файл результата отсутствует: {path}. Восстановите его в папке результата и повторите импорт.', path=files[value]))
        require(Path(value).suffix.lower() in {'.wav', '.mp3', '.flac'},
                tr('Формат файла результата не поддерживается для импорта: {name}. Поддерживаются WAV, MP3 и FLAC.', name=Path(value).name))
    data['operation'] = tr('Подключение к DaVinci Resolve')
    ctx = connect(logger, application)
    data['operation'] = tr('Проверка проекта и исходного клипа')
    validate_project(ctx, job)
    on_timeline = target == 'timeline'
    if on_timeline:
        require(ctx.timeline.GetUniqueId() == job.source_clip.get('timeline_id'),
                tr('Открыт другой таймлайн. Откройте исходный таймлайн этой задачи и повторите импорт.'))
    existing = matching_audio(ctx.timeline, paths) if on_timeline else {}
    pending = data.get('pending_placement') if on_timeline else None
    if pending and pending['path'] in existing:
        require(placement_matches(existing[pending['path']][1], pending),
                tr('Resolve разместил клип с другой длительностью или позицией. Проверьте новую отключённую дорожку вручную.'))
        data['pending_placement'] = None
        save_receipt(path, data)
    anchor = placement(ctx, job) if on_timeline and len(existing) < len(paths) else None
    if anchor and existing:
        # A retry must not mix stems from different source trims. Preserve any
        # user-edited items instead of silently resizing or duplicating them.
        expected = {'start': anchor[2], 'duration': (anchor[1] - anchor[0]) * anchor[3],
                    'source_start': anchor[0]}
        require(all(placement_matches(item, expected) for _, item in existing.values()),
                tr('После частичного импорта границы исходника и уже добавленных результатов различаются. '
                   'Согласуйте их на таймлайне или используйте импорт в Media Pool.'))
    data['operation'] = tr('Чтение Media Pool')
    pool = ctx.project.GetMediaPool()
    require(pool, tr('Media Pool недоступен. Дождитесь загрузки проекта и повторите импорт.'))
    root_folder, previous = pool.GetRootFolder(), pool.GetCurrentFolder()
    require(root_folder, tr('Media Pool недоступен. Дождитесь загрузки проекта и повторите импорт.'))
    media = {media_path(item): item for item in pool_items(root_folder)}
    destination = None
    try:
        for value in paths:
            data['operation'] = tr('Импорт файла «{name}» в Media Pool', name=Path(value).name)
            ensure_current(ctx, on_timeline)
            item = media.get(value)
            if item is None:
                if destination is None:
                    parent = subfolder(pool, root_folder, 'HVPC Resolve-MVSEP')
                    variant = f'{job.run_index:03d}' if job.run_index is not None else job.id[:8]
                    destination = subfolder(pool, parent, f'{Path(job.source_file).stem[:80]} — {variant} [{job.id[:8]}]')
                    data['bin_name'] = destination.GetName()
                require(pool.SetCurrentFolder(destination), tr('Resolve не открыл папку для импорта.'))
                ensure_current(ctx, on_timeline)
                imported = pool.ImportMedia([{'FilePath': files[value]}]) or []
                item = next((candidate for candidate in imported if media_path(candidate) == value), None)
                if item is None:
                    # Some 21.1 builds still accept only the documented legacy
                    # path form for ordinary audio. Reconcile before retrying.
                    item = next((candidate for candidate in pool_items(root_folder) if media_path(candidate) == value), None)
                if item is None:
                    imported = pool.ImportMedia([files[value]]) or []
                    item = next((candidate for candidate in imported if media_path(candidate) == value), None)
                require(item, tr('Resolve не импортировал файл: {name}', name=Path(files[value]).name))
                media[value] = item
            data['media_items'][value] = item.GetUniqueId()
            save_receipt(path, data)
        if not on_timeline:
            return
        # Validate every new stem before creating any tracks.
        specs = {}
        for value in paths:
            if value in existing:
                continue
            data['operation'] = tr('Проверка аудиофайла «{name}»', name=Path(value).name)
            props = media[value].GetClipProperty() or {}
            require(props.get('Type') == 'Audio',
                    tr('Resolve не распознал результат как аудиофайл: {name}. Файл доступен в Media Pool.', name=Path(value).name))
            require(str(props.get('Audio Ch')) in {'1', '2'},
                    tr('У результата «{name}» неподдерживаемое число аудиоканалов: {channels}. Поддерживаются моно и стерео. Файл доступен в Media Pool.',
                       name=Path(value).name, channels=props.get('Audio Ch') or tr('неизвестно')))
            fps = number(props.get('FPS'), tr('Resolve не сообщил частоту кадров аудиофайла.'))
            require(fps > 0, tr('Resolve не сообщил частоту кадров аудиофайла.'))
            # Audio endFrame is exclusive on the supported Resolve 21.1 bridge.
            # Verified with full, trimmed and fractional-position WAV clips;
            # subtracting one produces a one-frame-short stem in this build.
            start, end, record, timeline_fps = anchor
            # Reference a range of the full Media Pool item, not a rendered
            # excerpt or subclip. Both source handles remain available to trim.
            specs[value] = {'mediaPoolItem': media[value], 'startFrame': start * fps,
                            'endFrame': end * fps, 'recordFrame': record, 'mediaType': 2}
        appended_ids = {}
        for value in paths:
            data['operation'] = tr('Добавление файла «{name}» на новую аудиодорожку', name=Path(value).name)
            ensure_current(ctx, True)
            if value in existing:
                index, item = existing[value]
            else:
                # Revalidate the anchor immediately before a timeline mutation.
                require(placement(ctx, job, verify_file=False) == anchor,
                        tr('Положение или подрезка исходного клипа изменились во время добавления дорожек. Дождитесь завершения монтажа и повторите добавление.'))
                track_name = f'HVPC Resolve-MVSEP · {Path(files[value]).stem[:85]} [{job.id[:8]}]'
                reserved = data['reserved_tracks'].get(value)
                index = reserved.get('index', 0) if isinstance(reserved, dict) else 0
                reusable = (0 < index <= ctx.timeline.GetTrackCount('audio')
                            # Retain an owned empty reservation across product-name updates.
                            and ctx.timeline.GetTrackName('audio', index) == reserved.get('name', track_name)
                            and not ctx.timeline.GetItemListInTrack('audio', index))
                if not reusable:
                    index = ctx.timeline.GetTrackCount('audio') + 1
                    subtype = 'mono' if str(media[value].GetClipProperty('Audio Ch')) == '1' else 'stereo'
                    require(ctx.timeline.AddTrack('audio', subtype), tr('Resolve не создал аудиодорожку.'))
                    require(ctx.timeline.SetTrackName('audio', index, track_name), tr('Resolve не переименовал новую дорожку.'))
                    data['reserved_tracks'][value] = {'index': index, 'name': track_name}
                    save_receipt(path, data)
                require(ctx.timeline.SetTrackEnable('audio', index, False), tr('Resolve не отключил новую дорожку. Импорт остановлен.'))
                expected = {'path': value, 'start': anchor[2], 'duration': (anchor[1] - anchor[0]) * anchor[3], 'source_start': anchor[0]}
                data['pending_placement'] = expected
                save_receipt(path, data)
                ensure_current(ctx, True)
                require(placement(ctx, job, verify_file=False) == anchor,
                        tr('Положение или подрезка исходного клипа изменились во время добавления дорожек. Дождитесь завершения монтажа и повторите добавление.'))
                added = pool.AppendToTimeline([{**specs[value], 'trackIndex': index}]) or []
                require(len(added) == 1, tr('Resolve не разместил файл на дорожке: {name}', name=Path(value).name))
                item = added[0]
                appended_ids[value] = item.GetUniqueId()
                data['timeline_items'][value] = {'id': item.GetUniqueId(), 'track': index}
                save_receipt(path, data)
                require(placement_matches(item, expected),
                        tr('Resolve разместил клип с другой длительностью или позицией. Проверьте новую отключённую дорожку вручную.'))
                data['pending_placement'] = None
            data['timeline_items'][value] = {'id': item.GetUniqueId(), 'track': index}
            save_receipt(path, data)
        data['operation'] = tr('Проверка добавленных дорожек в Resolve')
        data['timeline_items'] = verify_timeline_results(ctx, files, appended_ids)
        data['timeline_name'] = ctx.timeline.GetName()
        data['timeline_id'] = ctx.timeline.GetUniqueId()
        data['timeline_verified_at'] = now()
        save_receipt(path, data)
    finally:
        # A disconnected bridge during cleanup must not hide the real failure.
        try:
            current = ctx.project_manager.GetCurrentProject()
            if previous and current and current.GetUniqueId() == ctx.project.GetUniqueId():
                pool.SetCurrentFolder(previous)
        except Exception:
            logger.exception('Could not restore the previous Media Pool folder.')
