# shared/api/client.py
# -*- coding: utf-8 -*-

from __future__ import annotations

from typing import Any, Optional, Dict

import httpx

from shared.config.settings import API_BASE_URL, HTTP_TIMEOUT_SECONDS
from shared.api.errors import ApiError


class ApiClient:
    """
    Minimal, no-auth client for Phase 1.
    Later we can add auth headers / tokens in one place.
    """

    def __init__(self, base_url: str = API_BASE_URL, timeout_seconds: float = HTTP_TIMEOUT_SECONDS):
        self.base_url = base_url.rstrip("/")
        self.timeout = httpx.Timeout(timeout_seconds)

    def _url(self, path: str) -> str:
        if not path.startswith("/"):
            path = "/" + path
        return f"{self.base_url}{path}"

    def request(self, method: str, path: str, json: Optional[dict] = None, params: Optional[dict] = None) -> Any:
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.request(method=method.upper(), url=self._url(path), json=json, params=params)
        except httpx.RequestError as e:
            raise ApiError(status_code=0, message=f"Network error: {e}") from e

        # Try to parse JSON; fallback to text
        payload: Any
        try:
            payload = resp.json()
        except Exception:
            payload = resp.text

        if resp.status_code >= 400:
            # FastAPI typically returns {"detail": "..."} or {"detail": [...]}
            msg = ""
            if isinstance(payload, dict) and "detail" in payload:
                msg = str(payload["detail"])
            else:
                msg = str(payload)
            raise ApiError(status_code=resp.status_code, message=msg, details=payload)

        return payload

    def get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        return self.request("GET", path, params=params)

    def post(self, path: str, json: Optional[dict] = None) -> Any:
        return self.request("POST", path, json=json)

    def put(self, path: str, json: Optional[dict] = None) -> Any:
        return self.request("PUT", path, json=json)

    def delete(self, path: str) -> Any:
        return self.request("DELETE", path)
