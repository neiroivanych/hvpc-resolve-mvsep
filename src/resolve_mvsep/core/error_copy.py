"""Translate known local failures without altering diagnostics or server messages."""
from .i18n import tr


def error_text(message):
    if message == 'No current project. Open a project in Resolve.':
        return tr('В DaVinci Resolve не открыт проект. Откройте проект, из которого запускалось разделение, и повторите импорт.')
    if message == 'No current timeline. Open a timeline in the Edit page.':
        return tr('В DaVinci Resolve не открыт таймлайн. Откройте исходный таймлайн и повторите импорт.')
    if message == 'Project Manager is unavailable. Wait for Resolve to finish loading.':
        return tr('Менеджер проектов Resolve недоступен. Дождитесь загрузки программы и повторите импорт.')
    if message == 'Resolve returned an invalid product version.':
        return tr('Resolve не сообщил версию программы. Повторите подключение перед импортом.')
    if str(message).startswith('Cannot load Resolve\'s native scripting bridge.'):
        return tr('Не удалось загрузить модуль подключения к Resolve. Запустите скрипт через встроенный ResolvePython и проверьте установку Resolve.')
    if str(message).startswith(('Resolve scripting module not found.', 'DaVinciResolveScript is unavailable.')):
        return tr('Не найден модуль сценариев Resolve. Проверьте установку Resolve и путь RESOLVE_SCRIPT_API.')
    if message == 'Output path is too long. Choose a shorter results folder.':
        return tr('Путь результата слишком длинный. Выберите папку с более коротким путём.')
    if str(message).startswith('Resolve is not available.'):
        return tr('Нет подключения к DaVinci Resolve. Откройте исходный проект и повторите импорт. Если Resolve уже открыт, проверьте External scripting: Local.')
    if message == 'Download time limit reached. The remote job can be resumed.':
        return tr('Истёк лимит времени скачивания. Разделение уже готово на MVSEP. Нажмите «Продолжить скачивание» — повторная отправка исходника не требуется.')
    return message or ''
