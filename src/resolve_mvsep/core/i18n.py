"""UI language only: independent of audio, recipes, profiles and API values."""
from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from .translations import EN

language = ContextVar("ui_language", default="ru")
LANGUAGES = ("ru", "en")


def tr(message: str, **values) -> str:
    template = EN.get(message, message) if language.get() == "en" else message
    return template.format(**values) if values else template


def tr_count(count: int, one: str, few: str, many: str) -> str:
    """Translate an integer count, including Russian 11–14 and 21/22/25 forms."""
    number = abs(count)
    if language.get() == "en":
        template = one if number == 1 else many
    elif 11 <= number % 100 <= 14:
        template = many
    else:
        template = one if number % 10 == 1 else few if 2 <= number % 10 <= 4 else many
    return tr(template, count=count)


@contextmanager
def using_language(value: str):
    token = language.set(value if value in LANGUAGES else "ru")
    try:
        yield
    finally:
        language.reset(token)


def load_language(root: Path) -> str:
    value = load_preferences(root).get("language")
    return value if value in LANGUAGES else "ru"


def load_preferences(root: Path) -> dict:
    try:
        data = json.loads((root / "config/ui.json").read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_language(root: Path, value: str) -> None:
    if value not in LANGUAGES:
        raise ValueError("Unsupported UI language")
    save_preferences(root, language=value)


def save_preferences(root: Path, **updates) -> None:
    data = {**load_preferences(root), **updates, "version": 1}
    path = root / "config/ui.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix="ui-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream, ensure_ascii=False)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
