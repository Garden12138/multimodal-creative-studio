from __future__ import annotations

import json
from typing import Protocol

import requests


class HttpResponse:
    def __init__(self, status_code: int, headers, content: bytes):
        self.status_code = status_code
        self.headers = {str(key).lower(): value for key, value in dict(headers or {}).items()}
        self.content = content or b""
        self.text = self.content.decode("utf-8", "replace")

    def json(self):
        return json.loads(self.text)


class Transport(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict | None = None,
        json_body: dict | None = None,
        timeout: tuple[float, float] | float = 30,
    ) -> HttpResponse:
        ...


class RequestsTransport:
    """Loopback provider calls must ignore HTTP(S)_PROXY."""

    def __init__(self):
        self.session = requests.Session()
        self.session.trust_env = False

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict | None = None,
        json_body: dict | None = None,
        timeout: tuple[float, float] | float = 30,
    ) -> HttpResponse:
        try:
            response = self.session.request(
                method,
                url,
                headers=headers,
                json=json_body,
                timeout=timeout,
            )
        except requests.Timeout as exc:
            raise TimeoutError("provider timeout") from exc
        except requests.RequestException as exc:
            raise ConnectionError("provider connection failed") from exc
        return HttpResponse(response.status_code, response.headers, response.content)
