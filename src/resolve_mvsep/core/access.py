"""Check known Premium requirements before any media upload or run reservation."""
from .i18n import tr
from .catalog import premium_requirements
from .errors import InspectionError
from ..mvsep.errors import MVSEPError


def check_access(spec, catalog, client):
    required = premium_requirements(spec, catalog)
    if not required:
        return
    selection = "; ".join(required)
    try:
        access = client.get_account_access()
    except MVSEPError as exc:
        raise InspectionError(tr('Не удалось проверить доступ Premium. Файл не отправлен. ') + str(exc)) from None
    if access.credits is not None and access.credits <= 0:
        raise InspectionError(tr('Для выбранных настроек нужен Premium: {v0}. Баланс кредитов в MVSEP: {v1:g}. Файл не отправлен. Выберите параметры без отметки Premium или проверьте баланс на MVSEP.', v0=selection, v1=access.credits))
    if access.premium_enabled is False:
        raise InspectionError(tr('Для выбранных настроек нужен Premium: {v0}. Использование Premium в аккаунте отключено. Файл не отправлен. Выберите параметры без отметки Premium или измените настройки на сайте MVSEP.', v0=selection))
    if access.premium_enabled is None or access.credits is None:
        raise InspectionError(tr('MVSEP не сообщил, доступен ли Premium, или не вернул баланс. Файл не отправлен; повторите проверку или выберите параметры без Premium.'))
