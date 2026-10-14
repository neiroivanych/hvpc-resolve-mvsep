"""Portable, readable provenance for one separation and each downloaded file."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .job import Job, now
from .profiles import spec_data
from .output import output_directory
from .setup import FORMAT_NAMES, cached_algorithms


def capture_settings(root: Path, job: Job, *, historical: bool = False, catalog: dict | None = None) -> dict:
    # Historical jobs have no contemporaneous catalog. Never relabel their
    # absent options as today's defaults or imply a server preset was expanded.
    algorithm = {} if historical else (cached_algorithms(root) if catalog is None else catalog).get(job.sep_type, {})
    fields = {field.get("name"): field for field in algorithm.get("algorithm_fields", [])
              if isinstance(field, dict) and isinstance(field.get("name"), str)}
    parameters = []
    for key in dict.fromkeys([*job.api_options, *fields]):
        field = fields.get(key, {})
        value = job.api_options.get(key)
        label = value
        try:
            choices = json.loads(field.get("options", "{}"))
            if isinstance(choices, dict):
                label = choices.get(value, value)
        except (ValueError, TypeError):
            pass
        parameters.append({"key": key, "name": field.get("text") or key,
                           "value": value, "label": label, "sent": key in job.api_options,
                           "conditional": bool(field.get("conditions"))})
    return {"captured_at": now(), "historical": historical,
            "catalog_available": bool(algorithm), "algorithm_name": algorithm.get("name"),
            "specification": spec_data(job.specification()), "profile_name": job.settings_profile,
            "parameters": parameters, "request_fields": job.specification().form_fields()}


def report_data(job: Job, *, audio_file: str | None = None) -> dict:
    snapshot = job.settings_snapshot or capture_settings(Path(), job, historical=True)
    return {"version": 1, "job_id": job.id, "run_index": job.run_index,
            "created_at": job.created_at,
            "source": {"file": job.source_file, "sha256": job.source_sha256},
            "project": job.source_clip.get("project_name"),
            "timeline": job.source_clip.get("timeline_name"),
            "track": job.source_clip.get("clip", {}).get("track_name"),
            "settings": snapshot, "actual_output_format": job.actual_output_format,
            "audio_file": audio_file, "result_files": [Path(path).name for path in job.result_files]}


def report_text(data: dict) -> str:
    snapshot, source = data["settings"], data["source"]
    spec = snapshot["specification"]
    number = f'{data["run_index"]:03d}' if data["run_index"] is not None else "не назначался (старый запуск)"
    lines = ["HVPC Resolve-MVSEP — настройки разделения", "", f"Вариант: {number}",
             f'Задача: {data["job_id"]}', f'Создана: {data["created_at"]}',
             f'Исходник: {source["file"]}', f'SHA-256 исходника: {source["sha256"] or "не записан"}',
             f'Проект: {data["project"] or "—"}', f'Дорожка: {data["track"] or "—"}']
    if data["audio_file"]:
        lines.append("Этот отчёт относится к файлу: " + data["audio_file"])
    if data.get("result_directory"):
        lines.append("Папка результата: " + data["result_directory"])
    lines.extend(["", f'Набор настроек: {snapshot["profile_name"] or "без именованного набора"}',
                  f'Режим: {spec["mode"]}'])
    if spec["preset_id"] is not None:
        lines.extend([f'Серверный пресет MVSEP: {spec["preset_id"]}',
                      "Его внутренние алгоритмы, параметры и формат не раскрыты в сохранённом запросе."])
    else:
        lines.extend([f'Алгоритм: {snapshot["algorithm_name"] or "название не зафиксировано"} (ID {spec["sep_type"]})',
                      f'Запрошенный формат: {FORMAT_NAMES[spec["output_format"]]} (код {spec["output_format"]})'])
    lines.extend([f'Формат в ответе MVSEP: {data["actual_output_format"] or "не сообщён"}',
                  "", "Параметры алгоритма:"])
    for field in snapshot["parameters"]:
        if field["sent"]:
            value = f'{field["label"]} (значение API: {field["value"]})'
        else:
            value = "не передавался; значение на сервере не зафиксировано"
            if field["conditional"]:
                value += " (условный параметр)"
        lines.append(f'- {field["name"]} [{field["key"]}]: {value}')
    if not snapshot["parameters"]:
        lines.append("Параметры не передавались; использовались значения сервера или серверного пресета.")
    if snapshot["historical"]:
        lines.append("Старый запуск: отсутствующие параметры нельзя восстановить из текущих значений MVSEP.")
    elif not snapshot["catalog_available"] and spec["preset_id"] is None:
        lines.append("Каталог был недоступен. Сохранены точные переданные значения; остальные неизвестны.")
    lines.extend(["", "Переданные поля запроса (без токена):",
                  json.dumps(snapshot["request_fields"], ensure_ascii=False, indent=2),
                  "", "Результаты одной попытки:", *["- " + name for name in data["result_files"]],
                  "", "Чтобы применить эти параметры к другому исходнику:",
                  "1. В окне результата задайте имя набора и нажмите «Сохранить набор».",
                  "2. Выберите другой исходник в Resolve, запустите скрипт и выберите этот набор в «Мои наборы настроек».",
                  "Также можно нажать «Из результата…» в окне нового разделения и выбрать этот .settings.json.",
                  "Настройки относятся ко всей попытке, включая vocals, other и остальные компоненты.",
                  "Для переноса эталона скопируйте аудиофайл вместе с его .settings.txt и .settings.json.",
                  "Номер объединяет компоненты одной попытки; повторное скачивание не создаёт новый вариант."])
    return "\n".join(lines) + "\n"


def atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".settings-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(text)
        os.replace(temporary, path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def write_reports(root: Path, job: Job, *, sidecars: bool = True) -> Path:
    """Do not modify Job or contact the server; also works for historical jobs."""
    base = root / "jobs" / job.id / "settings"
    data = report_data(job)
    data["result_directory"] = str(output_directory(root, job))
    atomic_text(base.with_suffix(".json"), json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    atomic_text(base.with_suffix(".txt"), report_text(data))
    if sidecars:
        output = output_directory(root, job)
        for filename in job.result_files:
            audio = Path(filename).resolve()
            # A stale/imported manifest must not write next to an arbitrary user file.
            if audio.parent != output or not audio.is_file():
                continue
            data = report_data(job, audio_file=audio.name)
            data["result_directory"] = str(output)
            atomic_text(audio.with_name(audio.name + ".settings.json"),
                        json.dumps(data, ensure_ascii=False, indent=2) + "\n")
            atomic_text(audio.with_name(audio.name + ".settings.txt"), report_text(data))
    return base.with_suffix(".txt")
