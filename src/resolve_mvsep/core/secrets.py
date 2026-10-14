"""Redaction at the formatting boundary, including exception tracebacks."""

from __future__ import annotations

import json
import logging
from urllib.parse import quote, quote_plus


class Redactor:
    def __init__(self, *secrets: str):
        variants = set()
        for secret in secrets:
            if secret:
                variants.update((secret, quote(secret, safe=""), quote_plus(secret),
                                 json.dumps(secret, ensure_ascii=True)[1:-1]))
        self._variants = tuple(sorted(variants, key=len, reverse=True))

    def clean(self, value: object) -> str:
        text = str(value)
        for secret in self._variants:
            text = text.replace(secret, "[REDACTED]")
        return text


class RedactingFormatter(logging.Formatter):
    def __init__(self, fmt: str, redactor: Redactor):
        super().__init__(fmt)
        self.redactor = redactor

    def format(self, record: logging.LogRecord) -> str:
        return self.redactor.clean(super().format(record))
