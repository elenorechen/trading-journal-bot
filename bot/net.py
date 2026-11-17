"""Shared HTTP session with retries."""
from __future__ import annotations

import time

import requests

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
})


def get(url: str, *, params: dict | None = None, retries: int = 3, timeout: int = 30) -> requests.Response:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            r = SESSION.get(url, params=params, timeout=timeout)
            r.raise_for_status()
            return r
        except requests.RequestException as e:  # network blips, 5xx, rate limits
            last = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"GET {url} 失敗：{last}")


def get_json(url: str, **kw):
    r = get(url, **kw)
    r.encoding = "utf-8-sig"
    try:
        return r.json()
    except ValueError:
        snippet = " ".join(r.text[:160].split())
        raise RuntimeError(
            f"{url} 回傳的不是 JSON（HTTP {r.status_code}, {r.headers.get('content-type')}）：{snippet!r}"
        ) from None


def get_text(url: str, **kw) -> str:
    r = get(url, **kw)
    r.encoding = "utf-8-sig"
    return r.text
