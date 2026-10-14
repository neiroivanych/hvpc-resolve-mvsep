"""Honest, testable progress text; no fabricated percentage or completion ETA."""

from __future__ import annotations

from .i18n import tr, language
from .error_copy import error_text

from dataclasses import dataclass
from datetime import datetime, timezone
import math
from pathlib import Path

from .active import TERMINAL
from .job import Job, JobStatus
from ..mvsep.models import PREPARATION_STATUSES, SEPARATION_STATUSES

STAGES = {
    JobStatus.CONFIGURING: "Выбор настроек разделения",
    JobStatus.CANCELLED: "Разделение отменено",
    JobStatus.PENDING: "Запуск задачи…",
    JobStatus.PREPARING: "Проверка исходного файла…",
    JobStatus.UPLOADING: "Отправка файла в MVSEP…",
    JobStatus.QUEUED: "Ожидание в очереди MVSEP…",
    JobStatus.PROCESSING: "MVSEP разделяет аудио…",
    JobStatus.DOWNLOADING: "Скачивание готовых файлов…",
    JobStatus.COMPLETED: "Готово — файлы скачаны",
    JobStatus.FAILED: "Выполнение остановлено",
}


def duration(seconds: float) -> str:
    seconds = max(0, round(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    return f"{hours}:{minutes:02}:{seconds:02}" if hours else f"{minutes:02}:{seconds:02}"


def age(timestamp: str, instant: datetime) -> float:
    return max(0, (instant - datetime.fromisoformat(timestamp)).total_seconds())


@dataclass(frozen=True)
class ProgressView:
    title: str
    elapsed: str
    detail: str
    active: bool
    completed: bool
    percent: float | None = None  # current transfer/chunk stage, never synthetic overall progress
    progress_label: str = "Процент готовности недоступен"
    remaining: str = ""


def megabytes(value: float) -> str:
    return decimal(value / 1_000_000) + tr(' МБ')


def decimal(value: float) -> str:
    number = f"{value:.1f}"
    return number.replace(".", ",") if language.get() == "ru" else number


def transfer_display(job: Job, instant: datetime, active: bool) -> tuple[float | None, str, str]:
    done, total = job.transfer_bytes, job.transfer_total
    percent = 100 * done / total if total and 0 <= done <= total else None
    size = megabytes(done) + (" / " + megabytes(total) if total else tr(' (размер неизвестен)'))
    name = tr('Отправлено') if job.status == JobStatus.UPLOADING else tr('Файл {v0} из {v1}', v0=job.transfer_file_index, v1=job.transfer_file_count)
    label = name + (f": {int(percent)}%" if percent is not None else "") + "  •  " + size
    fresh = active and job.transfer_updated_at and age(job.transfer_updated_at, instant) <= 10
    if percent == 100:
        remaining = (tr('Файл передан. Ожидаем подтверждения сервера.') if job.status == JobStatus.UPLOADING
                     else tr('Файл получен. Проверяем и сохраняем результат.'))
    elif fresh and job.transfer_eta is not None:
        estimate = duration(job.transfer_eta) if job.transfer_eta >= 1 else tr('меньше 1 с')
        operation = tr('отправки') if job.status == JobStatus.UPLOADING else tr('скачивания файла')
        remaining = tr('До завершения {v0}: ≈ {v1}', v0=operation, v1=estimate)
    elif not active:
        remaining = tr('Передача остановлена.')
    elif not fresh and job.transfer_bytes:
        remaining = tr('Нет новых данных о передаче. Оставшееся время пока неизвестно.')
    elif not total:
        remaining = tr('Сервер не сообщил размер файла; процент и оставшееся время неизвестны.')
    else:
        remaining = tr('Оставшееся время: рассчитываем по скорости передачи…')
    if fresh and job.transfer_rate and percent != 100:
        speed = (decimal(job.transfer_rate / 1000) + tr(' кБ/с')
                 if job.transfer_rate < 1_000_000 else megabytes(job.transfer_rate) + tr('/с'))
        remaining += "  •  " + speed
    return percent, label, remaining


def describe(job: Job, *, running: bool = True, instant: datetime | None = None) -> ProgressView:
    instant = instant or datetime.now(timezone.utc)
    terminal = job.status in TERMINAL
    active = not terminal and running
    end = datetime.fromisoformat(job.separation_completed_at or job.updated_at) if terminal else instant
    elapsed = tr('Прошло: ') + duration(age(job.processing_started_at or job.created_at, end))
    if job.stage_started_at and not terminal:
        elapsed += tr('  •  На этапе: ') + duration(age(job.stage_started_at, instant))
    title = tr(STAGES.get(job.status, job.status.value))
    if job.status == JobStatus.PROCESSING and job.remote_status == 'merging':
        title = tr('MVSEP собирает результат…')
    elif job.status == JobStatus.PROCESSING and job.remote_status in PREPARATION_STATUSES:
        title = tr('MVSEP подготавливает аудио…')
    elif job.status == JobStatus.PROCESSING and job.remote_status not in SEPARATION_STATUSES:
        title = tr('MVSEP: ожидаем результат…')
    percent = None
    progress_label = tr('Процент готовности MVSEP пока неизвестен')
    remaining = tr('Время до полного завершения неизвестно.')
    direction = {JobStatus.UPLOADING: "upload", JobStatus.DOWNLOADING: "download"}.get(job.status)
    if direction and job.transfer_direction == direction:
        percent, progress_label, remaining = transfer_display(job, instant, active)
        if job.status == JobStatus.UPLOADING and percent == 100:
            title = tr('Файл отправлен — ждём ответ MVSEP…')
    if (job.status == JobStatus.PROCESSING and job.remote_status == "distributing"
            and job.all_chunks and job.finished_chunks is not None
            and 0 <= job.finished_chunks <= job.all_chunks):
        percent = 100 * job.finished_chunks / job.all_chunks
        progress_label = tr('Готово частей: {v0} из {v1} ({v2}%)', v0=job.finished_chunks, v1=job.all_chunks, v2=int(percent))
        remaining = tr('Процент относится к частям аудио; объединение и скачивание ещё впереди.')
    if job.status == JobStatus.COMPLETED:
        percent, progress_label, remaining = 100, tr('Задача завершена — 100%'), tr('Все файлы скачаны.')
    elif job.status == JobStatus.CANCELLED:
        percent, progress_label, remaining = None, tr('Отменено до отправки файла'), ""
    elif job.status == JobStatus.FAILED or (not terminal and not running):
        percent, progress_label, remaining = None, tr('Обработка остановлена'), ""
        if job.remote_status == 'done':
            title = tr('Скачивание не завершено')
            progress_label = tr('Скачано файлов: {v0}', v0=len(job.result_files))
            remaining = tr('Разделение готово на MVSEP. Продолжите скачивание без повторной отправки исходника.')
    lines = []
    if job.run_index is not None:
        lines.append(tr('Вариант разделения {v0:03d}. Все файлы этой попытки имеют общий номер.', v0=job.run_index))
    if job.status == JobStatus.CANCELLED:
        lines.append(tr('Файл не отправлялся в MVSEP. Для новой задачи запустите скрипт из меню Resolve.'))
    elif job.status == JobStatus.CONFIGURING:
        progress_label, remaining = tr('Ожидаем выбора настроек'), ""
        lines.append(tr('Выберите формат и набор настроек, затем нажмите «Начать разделение».'))
    if not terminal and not running:
        title = tr('Фоновый процесс не работает')
        lines.append(tr('Последний сохранённый этап: ') + tr(STAGES.get(job.status, job.status.value)))
        lines.append(tr('Удалённая задача могла продолжить работу. Проверьте историю MVSEP; при сохранённом hash можно продолжить через --resume без повторной отправки.'))
    if job.status == JobStatus.UPLOADING:
        lines.append(tr('Шкала показывает ход отправки всего исходного файла. Отправка на 100% ещё не означает завершения разделения.'))
    if Path(job.source_file).suffix.lower() == '.mp4' and not terminal:
        lines.append(tr('MP4 передаётся целиком. MVSEP извлекает из него звук и разделяет выбранным алгоритмом.'))
    if job.status == JobStatus.QUEUED:
        if job.queue_position is not None:
            position = tr('Позиция этой задачи в очереди: {v0}', v0=job.queue_position)
            if job.queue_count is not None:
                position += f" / {job.queue_count}"
            lines.append(position)
        fresh = job.queue_checked_at and age(job.queue_checked_at, instant) <= 60
        if (fresh and type(job.account_wait_seconds) in (int, float)
                and math.isfinite(job.account_wait_seconds) and job.account_wait_seconds >= 0):
            if active:
                remaining = tr('До старта старейшей задачи аккаунта: ≈ ') + duration(job.account_wait_seconds)
            lines.append(tr('Оценка MVSEP до старта старейшей задачи аккаунта: ≈ ')
                         + duration(job.account_wait_seconds))
            lines.append(tr('Это оценка очереди аккаунта; она может относиться к другой вашей задаче.'))
        else:
            if active:
                remaining = tr('До старта: оценка MVSEP пока недоступна.')
            lines.append(tr('Время до старта: оценка MVSEP пока недоступна.'))
        lines.append(tr('Время до полного завершения сервис не сообщает.'))
    if job.remote_status == "distributing":
        lines.append(tr('Обработка частей аудио на серверах MVSEP.'))
    elif job.remote_status == "merging":
        lines.append(tr('MVSEP объединяет обработанные части аудио.'))
    if job.all_chunks and job.finished_chunks is not None:
        lines.append(tr('Готово частей: {v0} из {v1}', v0=job.finished_chunks, v1=job.all_chunks))
    if job.status == JobStatus.PROCESSING:
        lines.append(tr('Точное время до завершения неизвестно; ожидаем ответ MVSEP.'))
    if job.status in {JobStatus.DOWNLOADING, JobStatus.COMPLETED}:
        count = tr('Скачано файлов: {v0}', v0=len(job.result_files))
        if job.expected_files and job.status != JobStatus.COMPLETED:
            count += tr(' (в текущем результате: {v0})', v0=job.expected_files)
        lines.append(count)
    if job.status == JobStatus.COMPLETED:
        lines.append(tr('Нажмите «Открыть результат», чтобы перейти к скачанным файлам.'))
        lines.append(tr('«Настройки» — алгоритм и параметры этой попытки. Отчёты также лежат рядом с аудио.'))
        lines.append(tr('Добавление в Resolve выполняется кнопками «В Media Pool» или «На новые дорожки».'))
    elif job.error:
        lines.append(tr('Ошибка: ') + error_text(job.error))
    if job.remote_status and job.remote_status != 'done':
        lines.append(tr('Статус MVSEP: {status}', status=job.remote_status))
    if job.remote_message and job.status != JobStatus.COMPLETED:
        lines.append(tr('Сообщение MVSEP: ') + job.remote_message)
    if job.remote_checked_at and not terminal:
        seconds = age(job.remote_checked_at, instant)
        lines.append(tr('Последний ответ MVSEP: ') + duration(seconds) + tr(' назад.'))
        if seconds > 45:
            lines.append(tr('Свежего статуса пока нет: ожидаем ответ сети или повторяем запрос.'))
    if not lines:
        lines.append(tr('Подготавливаем задачу. Состояние обновляется автоматически.'))
    return ProgressView(title, elapsed, "\r\n\r\n".join(lines), active,
                        job.status == JobStatus.COMPLETED, percent, progress_label, remaining)
