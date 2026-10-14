"""Window lifetime is independent of the worker and the Resolve connection."""
from dataclasses import dataclass
from pathlib import Path

from .i18n import tr


@dataclass
class BackgroundState:
    busy: bool = False
    hidden: bool = False
    observed: bool = False

    def observe(self, busy: bool) -> bool:
        """Report a finished/interrupted operation once, including resumed work."""
        finished = self.observed and self.busy and not busy
        self.busy, self.observed = busy, True
        return finished

    def close_action(self, tray_available: bool) -> str:
        if not self.busy:
            return 'close'
        return 'hide' if tray_available else 'minimize'


def tray_tip(job, title: str) -> str:
    # Shell limits WCHAR buffers in UTF-16 code units, not Python characters.
    source = ' '.join(Path(job.source_file).name.split())
    text = f'HVPC Resolve-MVSEP\n{title}\n{source}'
    return text.encode('utf-16-le')[:254].decode('utf-16-le', errors='ignore')


def background_hint(busy: bool, tray_available: bool) -> str:
    if not busy:
        return ''
    if tray_available:
        return tr('Можно свернуть в фон. Вернуть окно: двойной щелчок по значку HVPC Resolve-MVSEP возле часов (или в меню ↑).')
    return tr('Значок возле часов недоступен. Окно свернётся на панель задач; обработка продолжится.')
