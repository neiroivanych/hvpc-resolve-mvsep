"""Extract only error-message fields; never dump an API response or request."""
from __future__ import annotations

import re

from ..core.secrets import Redactor


def error_details(payload, redactor: Redactor) -> str:
    if not isinstance(payload, dict):
        return ""
    messages = []

    def collect(value, depth=0):
        if depth > 4 or len(messages) >= 8:
            return
        if isinstance(value, str):
            # Redact before truncation, so partial tokens cannot leak at the boundary.
            text = " ".join(redactor.clean(value).split())[:500]
            if text and text not in messages:
                messages.append(text)
        elif isinstance(value, list):
            for item in value[:8]:
                collect(item, depth + 1)
        elif isinstance(value, dict):
            for key, item in list(value.items())[:12]:
                if str(key).casefold() in {"api_token", "token", "password", "authorization"}:
                    continue
                collect(item, depth + 1)

    data = payload.get("data")
    for container in (data, payload):
        if isinstance(container, dict):
            for key in ("message", "error", "errors", "detail"):
                collect(container.get(key))
        elif isinstance(container, str):
            collect(container)
    return re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", "; ".join(messages))[:1500]
