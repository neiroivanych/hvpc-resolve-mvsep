"""Optional daily allowance display. No raw account data or local counting."""
import queue
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .errors import InspectionError
from .i18n import tr
from .mvsep_config import load_mvsep_settings
from ..mvsep.client import MVSEPClient
from ..mvsep.errors import MVSEPError
from ..mvsep.models import DailyQuota


@dataclass(frozen=True)
class QuotaResult:
    status: str
    quota: DailyQuota | None = None


def read_quota(root: Path) -> QuotaResult:
    try:
        settings = load_mvsep_settings(root, require_token=False)
        if not settings.api_token:
            return QuotaResult('no_token')
        client = MVSEPClient(settings.api_token, request_timeout=8, max_get_retries=0)
        return QuotaResult('ready', client.get_daily_quota())
    except (InspectionError, MVSEPError, OSError):
        return QuotaResult('unavailable')


def quota_text(result: QuotaResult | None) -> str:
    if result is None:
        return tr('Бесплатные разделения\r\nна сегодня: проверяем…')
    if result.status == 'no_token':
        return tr('Бесплатные разделения\r\nнет API-ключа')
    if result.status != 'ready' or result.quota is None:
        return tr('Бесплатные разделения\r\nлимит недоступен')
    return tr('Бесплатные разделения\r\nна сегодня: {left} / {maximum}',
              left=result.quota.remaining, maximum=result.quota.maximum)


class QuotaMonitor:
    """One bounded background GET per minute, also after submission/completion."""
    def __init__(self, root: Path, *, loader=None, clock=time.monotonic, launch=None):
        self.root, self.loader, self.clock = root, loader or read_quota, clock
        self.launch = launch or (lambda task: threading.Thread(target=task, daemon=True).start())
        self.messages = queue.Queue()
        self.result = None
        self.fetching = False
        self.next_refresh = 0
        self.key = None
        self.refresh_requested = True

    def poll(self, key, *, visible=True):
        if key != self.key:
            self.key, self.refresh_requested = key, True
        try:
            self.result = self.messages.get_nowait()
        except queue.Empty:
            pass
        else:
            self.fetching = False
        if visible and not self.fetching and (self.refresh_requested or self.clock() >= self.next_refresh):
            self.fetching, self.refresh_requested = True, False
            self.next_refresh = self.clock() + 60
            def fetch():
                try:
                    result = self.loader(self.root)
                except Exception:
                    result = QuotaResult('unavailable')
                self.messages.put(result)
            self.launch(fetch)
        return self.result
