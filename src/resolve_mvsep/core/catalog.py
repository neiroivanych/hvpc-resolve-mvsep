"""Public MVSEP catalog, presentation and conditional per-job parameter choices."""
from __future__ import annotations

import json
import os
import re
import tempfile
from html.parser import HTMLParser
from pathlib import Path

from ..mvsep.client import MVSEPClient
from ..mvsep.models import Mode, SeparationSpec
from .errors import InspectionError
from .i18n import language, tr
from .catalog_ru import description_language, PARAGRAPHS
from .catalog_copy import catalog_blocks
from .description import Paragraph

GROUP_NAMES = {
    "Ensembles": "Ансамбли моделей", "Multistem": "Несколько инструментов",
    "Vocals / Instrumental": "Вокал и инструментал", "Drums, Bass and Synth": "Ударные, бас и синтезатор",
    "Keys": "Клавишные", "Guitars": "Гитары", "Plucked Strings": "Щипковые струнные",
    "Bowed Strings": "Смычковые струнные", "Wind": "Духовые", "Percussion": "Перкуссия",
    "Effects": "Речь, музыка и эффекты", "Upscale and Restoration": "Восстановление звука",
    "ASR and TTS": "Распознавание и синтез речи", "Experimental and Misc": "Экспериментальные и прочие",
    "Old Models": "Старые модели",
}
FIELD_NAMES = {
    "model type": "Модель", "vocal model type": "Модель вокала", "karaoke model type": "Модель караоке",
    "drums model type": "Модель ударных", "overlap": "Перекрытие фрагментов (Overlap)",
    "set of stems": "Набор дорожек", "output files": "Выходные файлы",
    "how to extract": "Способ извлечения", "extraction type": "Способ извлечения",
    "processing mode": "Режим обработки", "agressiveness": "Агрессивность обработки",
    "aggressiveness": "Агрессивность обработки", "text prompt": "Текст запроса",
    "bass model type": "Модель баса", "piano model type": "Модель фортепиано",
    "guitar model type": "Модель гитары", "organ model type": "Модель органа",
    "flute model type": "Модель флейты", "wind model type": "Модель духовых",
    "string model type": "Модель струнных", "cutoff (hz)": "Частота среза (Гц)",
    "extract vocals first": "Сначала извлечь вокал", "how to use": "Способ применения",
    "input sample rate": "Частота дискретизации входа", "preprocess": "Предварительная обработка",
    "version": "Версия", "guidance scale": "Сила следования условию (Guidance scale)",
    "language": "Язык", "transcription type": "Тип транскрипции",
}


def json_value(value, fallback):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return fallback
    return fallback if value is None else value


class _PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.ignored = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.ignored += 1
        if not self.ignored and tag in {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4"}:
            self.parts.append("\n")
        if tag in {"td", "th"}:
            self.parts.append(" | ")

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.ignored = max(0, self.ignored - 1)
        if not self.ignored and tag in {"p", "div", "li", "tr"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.ignored:
            self.parts.append(data)


def plain_text(value) -> str:
    parser = _PlainText()
    parser.feed(str(value or ""))
    return "\r\n".join(line for raw in "".join(parser.parts).splitlines()
                        if (line := " ".join(raw.split())))


def group_name(algorithm: dict) -> str:
    name = (algorithm.get("algorithm_group") or {}).get("name", tr('Прочие'))
    return GROUP_NAMES.get(name, name) if language.get() == "ru" else name


def description_fragments(algorithm: dict) -> tuple[str, str]:
    localized = {}
    for item in algorithm.get('algorithm_descriptions', []):
        actual_language = description_language(algorithm.get('render_id'), item)
        if actual_language != item.get('lang'):
            localized.setdefault(actual_language, item)
        else:
            localized[actual_language] = item  # explicitly tagged records win over the known repair
    def usable(value):
        text = str(value or "")
        return "" if plain_text(text).strip().casefold() in {"", "no data found"} else text
    candidates = [localized.get(lang, {}) for lang in dict.fromkeys((language.get(), "en", "ru"))]
    def field(name):
        return next((text for item in candidates if (text := usable(item.get(name)))), "")
    short = field("short_description") or usable(algorithm.get("description"))
    full = field("long_description")
    if not short and not full:
        short = tr('MVSEP пока не предоставил описание этого алгоритма.')
    return short, full


def description(algorithm: dict) -> str:
    short, full = map(plain_text, description_fragments(algorithm))
    original = short + (tr('\r\n\r\nОписание MVSEP:\r\n') + full if full and full != short else "")
    # The search field should find newly translated prose as well as the original
    # English terms. Keep raw model names/metrics searchable too.
    if language.get() == 'ru' and algorithm.get('render_id') in PARAGRAPHS:
        localized = '\n'.join(block.text for html in description_fragments(algorithm)
                              for block in catalog_blocks(html, algorithm.get('render_id')) if isinstance(block, Paragraph))
        if localized and localized != short and localized != original:
            return original + '\n' + localized
    return original


def field_label(field: dict) -> str:
    label = str(field.get("text") or field["name"])
    if label.casefold() in {"model type", "vocal model type"}:
        return "Model type"
    return FIELD_NAMES.get(label.casefold(), label) if language.get() == "ru" else label


def choices(field: dict) -> dict[str, str]:
    value = json_value(field.get("options"), {})
    return {str(k): plain_text(v) for k, v in value.items()} if isinstance(value, dict) else {}


def premium_option(field: dict, value: str) -> bool:
    # Official /js/audio-upload.js: any matching price rule marks Premium,
    # including multiplier 1.0. The multiplier alone is not a free-access flag.
    return any(isinstance(rule, dict) and str(rule.get("option_key")) == value
               for rule in field.get("price_rules") or [])


def premium_label(algorithm: dict) -> str:
    """Distinguish a paid algorithm from one containing paid parameter choices."""
    if algorithm.get("orientation") == 2:
        return " [Premium]"
    if any(premium_option(field, value) for field in fields(algorithm) for value in choices(field)):
        return tr(' [есть настройки Premium]')
    return ""


def option_label(algorithm: dict, field: dict, value: str, *, include_default=True) -> str:
    label = choices(field).get(value, value)
    if algorithm.get("orientation") == 2 or premium_option(field, value):
        label += " [Premium]"
    if include_default and value == str(field.get("default_key")):
        label += tr(' · по умолчанию')
    return label


def matching_options(algorithm: dict, query: str) -> list[str]:
    """Explain option-name matches without changing the user's selected recipe."""
    words = query.casefold().split()
    base = f"{algorithm.get('render_id', '')} {algorithm.get('name', '')} {group_name(algorithm)}".casefold()
    result = []
    for field in fields(algorithm):
        for value in choices(field):
            label = option_label(algorithm, field, value, include_default=False)
            text = f"{field_label(field)}: {label}"
            haystack = f"{base} {text}".casefold()
            if (words and all(word in haystack for word in words)
                    and any(word not in base and word in label.casefold() for word in words)):
                result.append(text)
    return result


def premium_requirements(spec: SeparationSpec, catalog: dict[int, dict]) -> list[str]:
    if spec.preset_id is not None:
        return []  # An opaque server preset has its own access rules.
    algorithm = catalog.get(spec.sep_type, {})
    result = [algorithm.get("name", str(spec.sep_type))] if algorithm.get("orientation") == 2 else []
    if not any(field.get("price_rules") for field in fields(algorithm)):
        return result
    for field in visible_fields(algorithm, spec.api_options):
        value = spec.api_options.get(field["name"], str(field.get("default_key")))
        if premium_option(field, value):
            result.append(f"{field_label(field)}: {choices(field).get(value, value)}")
    return result


def fields(algorithm: dict) -> list[dict]:
    return algorithm.get("algorithm_fields") or []


def validate_catalog(data) -> dict[int, dict]:
    if not isinstance(data, list) or not data or len(data) > 2000:
        raise InspectionError(tr('Некорректный каталог MVSEP. Сохранённая копия не заменена.'))
    result = {}
    for item in data:
        if (not isinstance(item, dict) or type(item.get("render_id")) is not int
                or item["render_id"] < 0 or not isinstance(item.get("name"), str)
                or item["render_id"] in result or not isinstance(item.get("algorithm_fields", []), list)
                or not isinstance(item.get("algorithm_group") or {}, dict)
                or not isinstance(item.get("algorithm_descriptions", []), list)):
            raise InspectionError(tr('Некорректная запись каталога MVSEP.'))
        if any(not isinstance(d, dict) for d in item.get("algorithm_descriptions", [])):
            raise InspectionError(tr('Некорректное описание алгоритма MVSEP.'))
        names = set()
        for field in fields(item):
            if (not isinstance(field, dict) or not isinstance(field.get("name"), str)
                    or not re.fullmatch(r"add_opt[1-9][0-9]*", field["name"])
                    or field["name"] in names):
                raise InspectionError(tr('Некорректные поля алгоритма MVSEP.'))
            names.add(field["name"])
        result[item["render_id"]] = item
    return result


def refresh_catalog(root: Path, client=None) -> dict[int, dict]:
    # Public GET only: never reads credentials or supplies media.
    client = client or MVSEPClient("", request_timeout=12, max_get_retries=0)
    data = client.get_algorithms()
    catalog = validate_catalog(data)
    path = root / "logs" / "mvsep_algorithms.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix="catalog-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return catalog


def filter_algorithms(catalog: dict[int, dict], group: str | None, query: str) -> list[int]:
    words = query.casefold().split()
    result = []
    for key, item in catalog.items():
        if group and group_name(item) != group:
            continue
        # Include all published choices, including conditional fields and Premium.
        # Searching never selects a model or alters the currently active options.
        options = " ".join(f"{field_label(field)} {option_label(item, field, value, include_default=False)}"
                           for field in fields(item) for value in choices(field))
        haystack = f"{key} {item['name']} {premium_label(item)} {group_name(item)} {description(item)} {options}".casefold()
        if all(word in haystack for word in words):
            result.append(key)
    return result  # preserve MVSEP's order, including its preferred models


def visible_fields(algorithm: dict, options: dict[str, str]) -> list[dict]:
    """Evaluate the published show/hide conditions; fail closed on unknown rules."""
    items = fields(algorithm)
    by_id = {field.get("id"): field for field in items if field.get("id") is not None}
    memo = {}

    def visible(field, stack):
        name = field["name"]
        if name in memo:
            return memo[name]
        if name in stack:
            raise InspectionError(tr('Циклическая зависимость параметров в каталоге MVSEP.'))
        rules = field.get("conditions") or []
        if not isinstance(rules, list):
            raise InspectionError(tr('Неизвестный формат условий MVSEP. Обновите каталог.'))
        shown = True
        for rule in rules:
            if not isinstance(rule, dict):
                raise InspectionError(tr('Неизвестное условие MVSEP.'))
            parent = by_id.get(rule.get("depends_on_field_id"))
            if parent is None:
                raise InspectionError(tr('Не найден связанный параметр MVSEP.'))
            if rule.get("operator") not in {"in", "not_in", "eq", "neq", "=", "==", "!="} or rule.get("action") not in {"show", "hide"}:
                raise InspectionError(tr('Новый тип зависимости MVSEP пока не поддерживается.'))
            targets = json_value(rule.get("value_json"), [])
            if not isinstance(targets, list):
                targets = [targets]
            value = options.get(parent["name"], parent.get("default_key"))
            match = str(value) in {str(v) for v in targets}
            if rule["operator"] in {"not_in", "neq", "!="}:
                match = not match
            match = visible(parent, stack | {name}) and match
            shown = shown and (match if rule["action"] == "show" else not match)
        memo[name] = shown
        return shown

    return [field for field in items if visible(field, set())]


def normalized_options(algorithm: dict, options: dict[str, str]) -> dict[str, str]:
    """On an explicit edit, remove inactive fields and pin all visible defaults."""
    result = {}
    for field in visible_fields(algorithm, options):
        key = field["name"]
        value = options.get(key, field.get("default_key"))
        if value is not None and (value != "" or field.get("input_type", "select") != "select"):
            result[key] = str(value)
    return result


def unsupported_reason(algorithm: dict) -> str:
    if not algorithm:
        return tr('Алгоритма нет в каталоге. Нажмите «Обновить каталог».')
    if algorithm.get("is_active", 1) != 1:
        return tr('MVSEP пометил этот алгоритм как неактивный. Выберите другой.')
    if (algorithm.get("audio_upload_disabled") or algorithm.get("audio_widget", "single_upload") != "single_upload"):
        return tr('Для этого алгоритма нужен отдельный сценарий ввода; он пока не поддерживается.')
    if algorithm.get("render_id") in {39, 64}:
        return tr('Этот алгоритм распознаёт речь и возвращает текст. Скрипт пока не поддерживает такие результаты. Premium не снимает это ограничение.')
    if algorithm.get("render_id") in {39, 64, 80, 103, 113, 114, 120, 127} or (algorithm.get("algorithm_group") or {}).get("name") == "ASR and TTS":
        return tr('Распознавание речи, синтез и MIDI пока не поддерживаются в этом окне разделения аудио.')
    if len(fields(algorithm)) > 4:
        return tr('У алгоритма больше четырёх полей. Для него требуется обновление интерфейса.')
    if any(field.get("input_type", "select") not in {"select", "text", "textarea"} for field in fields(algorithm)):
        return tr('Новый тип поля MVSEP пока не поддерживается.')
    return ""


def availability_label(algorithm: dict) -> str:
    """Account payment and support in this integration are independent states."""
    if algorithm and algorithm.get("is_active", 1) != 1:
        return tr(' [отключён на MVSEP]')
    return tr(' [не поддерживается скриптом]') if unsupported_reason(algorithm) else ""


def new_algorithm(spec: SeparationSpec, algorithm: dict) -> SeparationSpec:
    mode = Mode.SPEECH_MUSIC_SFX if algorithm["render_id"] in {18, 24, 36, 45, 56} else Mode.VOCALS_INSTRUMENTAL
    return SeparationSpec(mode, algorithm["render_id"], api_options=normalized_options(algorithm, {}),
                          output_format=spec.output_format)


def expected_outputs(algorithm: dict, options: dict[str, str]) -> dict[str, str]:
    """Catalog prediction only; actual download names always come from the result."""
    outputs = json_value(algorithm.get("outputs"), {})
    outputs = dict(outputs) if isinstance(outputs, dict) else {}
    active = visible_fields(algorithm, options)
    values = {field.get("server_key"): options.get(field["name"], field.get("default_key")) for field in active}
    variants = json_value(algorithm.get("output_variants"), {})
    for variant in variants.get("variants", []) if isinstance(variants, dict) else []:
        if (isinstance(variant, dict) and isinstance(variant.get("match"), dict)
                and all(str(values.get(k)) == str(v) for k, v in variant["match"].items())
                and isinstance(variant.get("outputs"), dict)):
            outputs = dict(variant["outputs"])
            break
    for field in active:
        overrides = json_value(field.get("output_overrides"), {})
        override = overrides.get(options.get(field["name"])) if isinstance(overrides, dict) else None
        if isinstance(override, dict) and isinstance(override.get("outputs"), dict):
            if override.get("mode") == "set":
                outputs = dict(override["outputs"])
            elif override.get("mode") == "add":
                outputs.update(override["outputs"])
    return {str(key): plain_text(value) for key, value in outputs.items()}


def validate_selection(spec: SeparationSpec, catalog: dict[int, dict]) -> None:
    if spec.preset_id is not None:
        return
    algorithm = catalog.get(spec.sep_type, {})
    if reason := unsupported_reason(algorithm):
        raise InspectionError(reason)
    visible = {field["name"]: field for field in visible_fields(algorithm, spec.api_options)}
    for key, value in spec.api_options.items():
        if key not in visible:
            raise InspectionError(tr('Параметр {v0} сейчас не применяется. Выберите алгоритм заново или измените модель.', v0=key))
        field = visible[key]
        if field.get("input_type", "select") == "select" and value not in choices(field):
            raise InspectionError(tr('Сохранённое значение «{v0}» отсутствует в каталоге. Выберите доступное значение.', v0=field_label(field)))
    for key, field in visible.items():
        if (field.get("required") or field.get("input_type", "select") == "select") and not spec.api_options.get(key, "").strip():
            raise InspectionError(tr('Выберите значение поля «{v0}».', v0=field_label(field)))


def parameter_help(algorithm: dict, options: dict[str, str]) -> str:
    lines = []
    for field in visible_fields(algorithm, options):
        label = str(field.get("text", "")).casefold()
        if label == "overlap":
            lines.append(tr('Overlap — насколько перекрываются обрабатываемые фрагменты. Большее значение требует больше вычислений; результат сравнивайте на своём аудио.'))
        if label in {"output files", "set of stems"}:
            lines.append(tr('Набор дорожек / Output files — какие компоненты и дополнительные результаты сохранить.'))
        for rule in field.get("price_rules") or []:
            if str(rule.get("option_key")) == options.get(field["name"]):
                if rule.get("mode") == "multiplier" and float(rule.get("value", 1)) != 1:
                    lines.append(tr('{v0}: коэффициент MVSEP для выбранного значения ×{v1:g}. Это не оценка времени ожидания.', v0=field_label(field), v1=float(rule['value'])))
    return "\r\n\r\n".join(lines)
