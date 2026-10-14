"""Reviewed display-only corrections; never alter cached/API model data or metrics."""
from dataclasses import replace

from .description import Paragraph, html_blocks
from .i18n import language, tr_count
from .catalog_ru import PARAGRAPHS, VOICE_EXAMPLES


def output_count_text(count: int) -> str:
    return tr_count(count, "По каталогу: {count} выходной компонент — ",
                    "По каталогу: {count} выходных компонента — ",
                    "По каталогу: {count} выходных компонентов — ")


# Exact prose fragments reviewed in MVSEP's RU descriptions. Tables, model/option
# labels, HTML links, request values and the original cache are left as supplied.
CORRECTIONS = {
    "улучшенные версия весов": "улучшенные версии весов",
    "проверерочных": "проверочных", "несколких": "нескольких", "смогда": "смогла",
    "большуюю": "большую", "артифакты": "артефакты",
    "лучше чем": "лучше, чем", "чем больше тем лучше": "чем больше, тем лучше",
    "так же специальные": "также специальные",
    "Так же отдельно": "Также отдельно", "так же приведены": "также приведены",
    "Так же авторы": "Также авторы",
    "пианино, устранение эффектов": "пианино, устранения эффектов",
    "вокал выбрав опцию": "вокал, выбрав опцию",
    "Во втором случае, трек": "Во втором случае трек",
    "В первом варианте, нейросетевая": "В первом варианте нейросетевая",
    "две части вокальную и инструментальную": "две части: вокальную и инструментальную",
    "на коде опубликованном": "на коде, опубликованном",
    "результаты похожие на те": "результаты, похожие на те",
    "Demucs4 HT которая": "Demucs4 HT, которая",
    "семейству моделей основанных": "семейству моделей, основанных",
    "эмбеддинги основанные": "эмбеддинги, основанные",
    "3 вариантах основанных": "3 вариантах, основанных",
    "выбрать между извлечением напрямую из микса или": "выбрать между извлечением напрямую из микса и",
    "включает акустическую и электронную": "включает акустическую и электрическую",
    "Модель MVSep Bowed Strings это": "Модель MVSep Bowed Strings — это",
    "метрики качества полученные": "метрики качества, полученные",
    "модель предложенная": "модель, предложенная",
    "аналогичных метрик как у авторов": "таких же метрик, как у авторов",
    "все остальное": "всё остальное", "и.т.д.": "и т. д.", "и т.д.": "и т. д.",
    "моно-сигнал": "моносигнал", "микро-колебаний": "микроколебаний",
}
HEADINGS = {
    "Quality table": "Таблица качества",
    "Detailed statistics on Multisong dataset:": "Подробная статистика на наборе данных Multisong:",
    "Детальная статистика на Multisong датасете:": "Подробная статистика на наборе данных Multisong:",
    "Детальная статистика на Multisong dataset:": "Подробная статистика на наборе данных Multisong:",
    "Подробная статистика на Multisong dataset:": "Подробная статистика на наборе данных Multisong:",
}


def catalog_blocks(html: str, algorithm_id: int) -> list:
    blocks = html_blocks(html)
    if language.get() != "ru":
        return blocks
    result = []
    for block in blocks:
        if isinstance(block, Paragraph):
            text = HEADINGS.get(block.text, block.text)
            text = PARAGRAPHS.get(algorithm_id, {}).get(' '.join(text.split()), text)
            for before, after in CORRECTIONS.items():
                text = text.replace(before, after)
            if algorithm_id == 39:
                # In the Whisper description only: other algorithms legitimately
                # discuss whispering as a vocal technique.
                text = text.replace("модель шепота", "модель Whisper").replace("Модель шепота", "Модель Whisper")
                text = text.replace("шепота сначала", "Whisper сначала")
                text = text.replace("применить Модель", "применить модель")
            if algorithm_id in {10, 45}:
                prefixes = {'More information in official repository:': 'Подробнее в официальном репозитории:',
                            'Paper:': 'Статья:', 'Link:': 'Ссылка:'}
                text = '\n'.join(next((replacement + line[len(prefix):] for prefix, replacement in prefixes.items()
                                       if line.startswith(prefix)), line) for line in text.splitlines())
            if algorithm_id == 103:
                text = text.replace('Советы для записи на английском (Tips for recording):', 'Советы для записи на английском:')
                text = text.replace('Pronunciation (Произношение):', 'Произношение:')
                text = text.replace('Flow (Поток):', 'Плавность речи:').replace('(voice melody)', '(интонация)')
                text = text.replace('Breathing (Дыхание):', 'Дыхание:')
            block = replace(block, text=text)
        result.append(block)
        if algorithm_id == 103 and isinstance(block, Paragraph):
            translated = VOICE_EXAMPLES.get(' '.join(block.text.split()))
            if translated:
                result.append(Paragraph('Перевод примера (для понимания): ' + translated))
    return result
