"""HTTP client for the separately deployed SiteCheck persistence API."""
from __future__ import annotations

import os
import time
from typing import Any
from urllib.parse import quote, urlsplit

import requests


class NoticeApiError(Exception):
    def __init__(self, message: str, *, status: int = 0, code: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.code = code


class NoticeApi:
    def __init__(self, base_url: str, *, auth_header: str = "Authorization",
                 auth_value: str = "", timeout: float = 15.0,
                 session: requests.Session | None = None) -> None:
        parsed = urlsplit(base_url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.username:
            raise ValueError("SITECHECK_API_BASE_URL must be an HTTP(S) URL without credentials")
        if not auth_header.isascii() or not auth_header.replace("-", "").isalnum():
            raise ValueError("Invalid API authentication header name")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = session or requests.Session()
        self.headers = {"Accept": "application/json"}
        if auth_value:
            self.headers[auth_header] = auth_value

    @classmethod
    def from_env(cls) -> NoticeApi | None:
        base = os.environ.get("SITECHECK_API_BASE_URL", "").strip()
        if not base:
            return None
        return cls(base, auth_header=os.environ.get("SITECHECK_API_AUTH_HEADER", "Authorization"),
                   auth_value=os.environ.get("SITECHECK_API_AUTH_VALUE", ""),
                   timeout=float(os.environ.get("SITECHECK_API_TIMEOUT", "15")))

    def _request(self, method: str, suffix: str = "", *, body: Any = None,
                 params: dict[str, Any] | None = None) -> Any:
        url = self.base_url + "/api/v1/site-check-items" + suffix
        retryable = method == "GET" or (method == "POST" and
                                          suffix in ("", ":lookup", ":import"))
        for attempt in range(3 if retryable else 1):
            try:
                response = self.session.request(method, url, json=body, params=params,
                                                headers=self.headers, timeout=self.timeout)
            except requests.RequestException as exc:
                if attempt == 2 or not retryable:
                    raise NoticeApiError(f"API connection failed: {exc}") from exc
                time.sleep(0.25 * (2 ** attempt))
                continue
            if response.status_code in (502, 503, 504) and retryable and attempt < 2:
                time.sleep(0.25 * (2 ** attempt))
                continue
            break
        if not response.ok:
            try:
                payload = response.json()
            except ValueError:
                payload = None
            error = payload.get("error", {}) if isinstance(payload, dict) else {}
            raise NoticeApiError(str(error.get("message") or f"HTTP {response.status_code}"),
                                 status=response.status_code,
                                 code=str(error.get("code") or {
                                     401: "UNAUTHORIZED", 403: "FORBIDDEN"
                                 }.get(response.status_code, "")))
        try:
            payload = response.json()
        except ValueError as exc:
            raise NoticeApiError("API returned non-JSON response", status=response.status_code) from exc
        if not isinstance(payload, dict) or "data" not in payload:
            raise NoticeApiError("API response is missing data", status=response.status_code)
        return payload["data"]

    def lookup(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return self._request("POST", ":lookup", body={"items": items})["results"]

    def candidates(self, item: dict[str, Any]) -> list[dict[str, Any]]:
        params: dict[str, Any] = {key: item[key] for key in
                                  ("checkType", "institutionCode", "scheduleText")}
        params.update(includeDeleted="true", limit=200)
        found: list[dict[str, Any]] = []
        while True:
            page = self._request("GET", params=params)
            found.extend(page["items"])
            if not page.get("hasNext"):
                return found
            cursor = page.get("nextBeforeId")
            if not cursor or cursor == params.get("beforeId"):
                raise NoticeApiError("API candidate pagination did not advance")
            params["beforeId"] = cursor

    def import_items(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return self._request("POST", ":import", body={"items": items})["results"]

    def create(self, item: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", body=item)

    def get(self, item_id: str, *, include_deleted: bool = False) -> dict[str, Any]:
        params = {"includeDeleted": "true"} if include_deleted else None
        return self._request("GET", "/" + quote(str(item_id), safe=""), params=params)

    def patch(self, item_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        return self._request("PATCH", "/" + quote(str(item_id), safe=""), body=changes)

    def delete(self, item_id: str) -> dict[str, Any]:
        return self._request("DELETE", "/" + quote(str(item_id), safe=""))


def main() -> None:
    api = NoticeApi.from_env()
    if api is None:
        raise SystemExit("SITECHECK_API_BASE_URL is required")
    results = api.lookup([])
    if results != []:
        raise SystemExit("Unexpected lookup response for an empty batch")
    print("API connectivity and empty lookup: OK")


if __name__ == "__main__":
    main()
