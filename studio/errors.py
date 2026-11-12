from __future__ import annotations

import re


class StudioError(Exception):
    def __init__(self, code: str, message: str, http_status: int = 400, task_id: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status
        self.task_id = task_id


def redact(text: str, secrets: list[str] | None = None, limit: int = 500) -> str:
    redacted = text or ""
    for secret in secrets or []:
        if secret:
            redacted = redacted.replace(secret, "[REDACTED]")
    redacted = re.sub(r"Bearer\s+\S+", "Bearer [REDACTED]", redacted, flags=re.IGNORECASE)
    return redacted[:limit]
