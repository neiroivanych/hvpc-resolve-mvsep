"""Reviewed Russian prose for English-only fragments in the public MVSEP catalog.

Keys are exact rendered source paragraphs (whitespace normalized by the caller).
An unknown/updated paragraph is left intact rather than replaced with stale copy.
Model identifiers, option values and metric tables are never translated here.
"""

PARAGRAPHS = {
    10: {
        'Algorithm Demucs3 (A and B versions)': 'Алгоритм Demucs3 (версии A и B).',
        'Algorithm Demucs3 splits track into 4 stems (bass, drums, vocals, other). The winner of the Music Demuxing Challenge 2021.':
            'Алгоритм Demucs3 разделяет запись на 4 компонента: бас, ударные, вокал и остальные инструменты. Победитель конкурса Music Demuxing Challenge 2021.',
        'Note: For version A only MUSDB18 training data was used for training, so quality is worse than Demucs3 Model B. Demucs3 Model A and Demucs3 Model B has the same architecture, but has different weights.':
            'Примечание: версия A обучалась только на обучающей части набора MUSDB18, поэтому качество ниже, чем у Demucs3 Model B. У Demucs3 Model A и Demucs3 Model B одинаковая архитектура, но разные веса модели.',
    },
    30: {
        "It's Ensemble (vocals, instrum, bass, drums, other) + more models included like guitars, piano, wind, strings, back/lead vocals and drumsep.":
            'Ансамбль моделей Ensemble для выделения вокала, инструментала, баса, ударных и остальных инструментов. Дополнительно включены модели для гитар, фортепиано, духовых и струнных инструментов, бэк-вокала и основного вокала, а также разделения ударных (drumsep).',
    },
    45: {
        'Bandit v2 is a model for cinematic audio source separation in 3 stems: speech, music, effects/sfx. It was trained on DnR v3 dataset.':
            'BandIt v2 — модель для разделения звука в кино на три компонента: речь, музыку и звуковые эффекты (SFX). Модель обучена на наборе данных DnR v3.',
    },
    88: {
        'Synth extraction model': 'Модель для выделения синтезаторных партий.',
        'Synth extraction model. Synth is included the following stems: Synth, Synthesizer, Synth Pad, Synth Bass, Synth Vocals, Synth Strings, Synth Percussion, Synth FX, Synth Keys, Synth Brass, Synth Guitar, Synth Flute, Synth Ambiant':
            'Модель для выделения синтезаторных партий. К ним относятся следующие категории: '
            'Synth и Synthesizer — синтезатор; Synth Pad — синтезаторные пэды; Synth Bass — синтезаторный бас; '
            'Synth Vocals — синтезированный вокал; Synth Strings — синтезированные струнные; '
            'Synth Percussion — синтезированная перкуссия; Synth FX — синтезаторные звуковые эффекты; '
            'Synth Keys — синтезаторные клавишные; Synth Brass — синтезированные медные духовые; '
            'Synth Guitar — синтезированная гитара; Synth Flute — синтезированная флейта; '
            'Synth Ambiant — синтезаторные эмбиентные партии.',
    },
    103: {
        'Option 1: Universal (Balanced & Clear)': 'Вариант 1: универсальный (сбалансированный и чёткий)',
        'Option 2: Conversational (Vlog & Social Media)': 'Вариант 2: разговорный (видеоблоги и соцсети)',
        'Option 3: Professional (Business & Narration)': 'Вариант 3: профессиональный (деловые задачи и озвучка)',
        'Вот три варианта на английском под разные задачи:':
            'Ниже — три английских текста для записи эталонного голоса под разные задачи. Русский перевод добавлен для понимания смысла; для записи используйте английский оригинал.',
    },
}

# VibeVoice's source explicitly requires English/Chinese. Preserve the English
# recording scripts and append clearly labelled translations, not replacement input.
VOICE_EXAMPLES = {
    '"To create a perfect voice clone, the AI needs to hear a full range of phonetic sounds. I am speaking clearly, taking small pauses, and asking: can you hear every detail? This short sample captures the unique texture and tone of my voice."':
        '«Чтобы создать точную копию голоса, искусственному интеллекту нужно услышать полный набор звуков речи. Я говорю чётко, делаю небольшие паузы и спрашиваю: слышите ли вы каждую деталь? Этот короткий образец передаёт уникальный тембр и звучание моего голоса».',
    '"Hey! I’m recording this clip to test how well the new technology works. The secret is to relax and speak exactly like I would to a friend. Do you think the AI can really copy my style and energy in just fifteen seconds?"':
        '«Привет! Я записываю этот фрагмент, чтобы проверить, насколько хорошо работает новая технология. Секрет в том, чтобы расслабиться и говорить так же, как с другом. Как думаете, сможет ли искусственный интеллект повторить мою манеру речи и энергичность всего за пятнадцать секунд?»',
    '"Voice synthesis technology is rapidly changing how we communicate in the digital age. It is essential to speak with confidence and precision to ensure high-quality output. This brief recording provides all the necessary data for a professional and accurate digital clone."':
        '«Технология синтеза голоса стремительно меняет наше общение в цифровую эпоху. Для качественного результата важно говорить уверенно и чётко. Эта короткая запись содержит все необходимые данные для создания профессиональной и точной цифровой копии голоса».',
}


def description_language(algorithm_id: int, item: dict) -> str | None:
    """Correct one known mislabeled RU record without rewriting service/cache data."""
    lang = item.get('lang')
    if algorithm_id == 30 and lang == 'en' and str(item.get('long_description', '')).startswith('<p>Это Ensemble'):
        return 'ru'
    return lang
