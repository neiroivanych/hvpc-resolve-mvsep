"""Read-only connection state, separate from download and import outcomes."""
from dataclasses import dataclass

from .i18n import tr


@dataclass(frozen=True)
class ResolveStatus:
    code: str = 'checking'

    @property
    def pool_allowed(self):
        return self.code in {'ready', 'wrong_timeline', 'old_version'}

    @property
    def timeline_allowed(self):
        return self.code == 'ready'

    @property
    def tone(self):
        return 'connected' if self.code == 'ready' else 'muted' if self.code == 'checking' else 'warning'

    def copy(self, job):
        project = job.source_clip.get('project_name') or '—'
        timeline = job.source_clip.get('timeline_name') or '—'
        if self.code == 'ready':
            return (tr('DaVinci Resolve подключён'),
                    tr('Проект «{name}». Выберите способ добавления файлов кнопками ниже.', name=project) if job.status.value == 'completed' else
                    tr('Проект «{name}». Импорт доступен после скачивания файлов.', name=project))
        if self.code == 'checking':
            return tr('Проверяем подключение к DaVinci Resolve…'), tr('Обработка и скачивание работают независимо от Resolve.')
        if self.code in {'wrong_project', 'no_project'}:
            return tr('Импорт недоступен — откройте исходный проект'), tr('Откройте в Resolve проект «{name}». Проверка повторяется автоматически.', name=project)
        if self.code in {'wrong_timeline', 'no_timeline'}:
            return (tr('Откройте исходный таймлайн: {name}', name=timeline),
                    tr('Media Pool доступен. Для новых дорожек откройте исходный таймлайн.') if self.code == 'wrong_timeline' else
                    tr('Откройте таймлайн в Resolve. Проверка повторяется автоматически.'))
        if self.code == 'old_version':
            return tr('Доступен импорт в Media Pool'), tr('Для размещения на дорожках требуется Resolve 21.1 или новее.')
        if self.code == 'no_context':
            return tr('У задачи нет привязки к проекту Resolve'), tr('Откройте папку результата и добавьте файлы в проект вручную.')
        return (tr('Нет подключения к DaVinci Resolve'),
                tr('Файлы сохранены. Для импорта откройте Resolve и исходный проект.') if job.status.value == 'completed' else
                tr('Скачивание не зависит от Resolve. Для импорта откройте исходный проект.'))
