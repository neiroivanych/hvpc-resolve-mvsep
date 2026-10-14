"""A stage panel driven only by saved worker/server telemetry, never a fake countdown."""
from dataclasses import dataclass
from datetime import datetime, timezone
import math

from .i18n import tr
from .job import Job, JobStatus
from .progress import ProgressView, duration
from ..mvsep.models import PREPARATION_STATUSES, SEPARATION_STATUSES


def stage_labels() -> tuple[str, ...]:
    return (tr('Подготовка'), tr('Очередь'), tr('Разделение'), tr('Сборка'), tr('Скачивание'))


@dataclass(frozen=True)
class PipelineView:
    step: int | None
    caption: str
    value: str
    secondary: str = ""
    note: str = ""
    tone: str = "working"
    numeric: bool = False
    animate: bool = False


def fresh(timestamp: str | None, instant: datetime, seconds: int) -> bool:
    try:
        return timestamp is not None and 0 <= (instant - datetime.fromisoformat(timestamp)).total_seconds() <= seconds
    except (ValueError, TypeError):
        return False


def pipeline_view(job: Job, progress: ProgressView, *, instant: datetime | None = None) -> PipelineView:
    instant = instant or datetime.now(timezone.utc)
    if progress.completed:
        return PipelineView(5, tr('РАЗДЕЛЕНИЕ ЗАВЕРШЕНО'), tr('Файлы готовы'),
                            tr('Скачано файлов: {v0}', v0=len(job.result_files)),
                            tr('Откройте результат или сохраните эти настройки как набор.'), 'done')
    if not progress.active and job.remote_status == 'done' and job.remote_hash:
        return PipelineView(4, tr('РАЗДЕЛЕНИЕ ГОТОВО НА MVSEP'), tr('Скачивание остановлено'),
                            tr('Скачано файлов: {v0}', v0=len(job.result_files)),
                            tr('Нажмите «Продолжить скачивание». Готовые файлы сохранятся.'), 'error')
    if not progress.active:
        cancelled = job.status == JobStatus.CANCELLED
        return PipelineView(None, tr('ОТМЕНЕНО') if cancelled else tr('ВЫПОЛНЕНИЕ ОСТАНОВЛЕНО'),
                            tr('Файл не отправлен') if cancelled else tr('Проверьте подробности'),
                            note=tr('Подробности сохранены в папке задачи.'), tone='idle' if cancelled else 'error')
    if job.status == JobStatus.QUEUED:
        current = fresh(job.remote_checked_at, instant, 45)
        estimate = job.account_wait_seconds
        known = (current and fresh(job.queue_checked_at, instant, 60)
                 and type(estimate) in (int, float) and math.isfinite(estimate) and estimate >= 0)
        position = tr('MVSEP пока не сообщил позицию в очереди.')
        if current and job.queue_position is not None:
            position = (tr('Позиция в очереди: {position} из {total}', position=job.queue_position, total=job.queue_count)
                        if job.queue_count is not None else tr('Позиция в очереди: {position}', position=job.queue_position))
        note = (tr('Оценка до старта первой ожидающей задачи аккаунта.') if known else
                tr('MVSEP пока не сообщил оценку ожидания.') if current else tr('Ожидаем свежие данные от MVSEP.'))
        clock = duration(estimate) if known else '—'
        if len(clock) == 5 and clock.startswith('0'):
            clock = clock[1:]
        return PipelineView(1, tr('ПРИМЕРНОЕ ВРЕМЯ ОЖИДАНИЯ'), clock,
                            position if current else tr('Позиция в очереди пока неизвестна.'), note, 'queue', True, current)
    if job.status == JobStatus.PROCESSING:
        current = fresh(job.remote_checked_at, instant, 45)
        note = (tr('Обработка продолжается на серверах MVSEP.') if current else tr('Ожидаем свежие данные от MVSEP.'))
        if job.remote_status in PREPARATION_STATUSES:
            return PipelineView(0, tr('ПОДГОТОВКА НА MVSEP'), tr('Подготавливаем аудио'),
                                tr('Сервер подготавливает файл перед разделением.'), note, animate=current)
        if job.remote_status not in SEPARATION_STATUSES:
            return PipelineView(None, tr('ОЖИДАЕМ РЕЗУЛЬТАТ MVSEP'), tr('Проверяем состояние'),
                                tr('Сервер сообщил дополнительный этап обработки.'),
                                tr('Ожидание продолжается. Повторная отправка не требуется.'), animate=current)
        if job.remote_status == 'merging':
            return PipelineView(3, tr('СБОРКА РЕЗУЛЬТАТА'), tr('Объединяем части аудио'),
                                tr('После сборки начнётся скачивание.'), note, animate=current)
        parts = current and job.remote_status == 'distributing' and progress.percent is not None
        return PipelineView(2, tr('РАЗДЕЛЕНИЕ АУДИО'), f'{int(progress.percent)}%' if parts else tr('Разделяем аудио'),
                            progress.progress_label if parts else tr('MVSEP обрабатывает ваш файл.'),
                            note, numeric=parts, animate=current)
    if job.status in {JobStatus.UPLOADING, JobStatus.DOWNLOADING}:
        upload = job.status == JobStatus.UPLOADING
        percent = progress.percent
        return PipelineView(0 if upload else 4, tr('ОТПРАВКА ФАЙЛА') if upload else tr('СКАЧИВАНИЕ РЕЗУЛЬТАТА'),
                            f'{int(percent)}%' if percent is not None else tr('Отправляем файл') if upload else tr('Получаем файлы'),
                            tr('Из Resolve в MVSEP') if upload else tr('Из MVSEP в выбранную папку'),
                            tr('Это прогресс передачи файла.') if percent is not None else tr('Размер передачи пока неизвестен.'),
                            numeric=percent is not None, animate=True)
    return PipelineView(0, tr('ПОДГОТОВКА'), tr('Готовим задачу'),
                        tr('Проверяем файл и настройки.'), animate=True)
