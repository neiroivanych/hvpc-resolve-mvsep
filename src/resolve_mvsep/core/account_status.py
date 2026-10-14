"""Small, read-only balance indicator; never retain the raw account response."""
from dataclasses import dataclass
from pathlib import Path

from .errors import InspectionError
from .i18n import tr
from .mvsep_config import load_mvsep_settings
from ..mvsep.client import MVSEPClient
from ..mvsep.errors import MVSEPError


@dataclass(frozen=True)
class BalanceResult:
    status: str
    credits: float | None = None


def read_balance(root: Path) -> BalanceResult:
    try:
        settings = load_mvsep_settings(root, require_token=False)
        if not settings.api_token:
            return BalanceResult("no_token")
        client = MVSEPClient(settings.api_token, request_timeout=8, max_get_retries=0)
        access = client.get_account_access()
        return BalanceResult("ready", access.credits)
    except (InspectionError, MVSEPError, OSError):
        # Neither identity nor API/server/config error details belong in this label.
        return BalanceResult("unavailable")


def balance_text(result: BalanceResult | None, loading: bool = False) -> str:
    if loading:
        return tr('Баланс MVSEP: проверяем…')
    if result is None or result.status == "unavailable":
        return tr('Баланс MVSEP: недоступен')
    if result.status == "no_token":
        return tr('Баланс MVSEP: нет API-ключа')
    if result.credits is None:
        return tr('Баланс MVSEP: нет данных')
    return tr('Баланс MVSEP (кредиты): {credits:g}', credits=result.credits)
