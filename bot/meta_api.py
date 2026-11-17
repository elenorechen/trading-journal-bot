"""發文到 Threads 與 Instagram（Meta 官方 API）。

需要的 GitHub Secrets：
  THREADS_ACCESS_TOKEN   Threads 長效權杖（threads_basic, threads_content_publish）
  IG_ACCESS_TOKEN        Instagram 長效權杖（instagram_business_basic, instagram_business_content_publish）

圖片必須是公開網址（這裡用 GitHub raw 網址），而且 IG 只接受 JPEG。
"""
from __future__ import annotations

import time

import requests

from .settings import CONFIG, env

THREADS_LIMIT = 500


def _ver(key: str, default: str) -> str:
    return CONFIG.get("meta", {}).get(key, default)


def _call(method: str, url: str, **params) -> dict:
    # POST 用表單內容送出，避免長篇中文塞進網址超過長度限制
    if method == "POST":
        r = requests.post(url, data=params, timeout=60)
    else:
        r = requests.get(url, params=params, timeout=60)
    try:
        js = r.json()
    except ValueError:
        js = {"raw": r.text}
    if r.status_code >= 400 or "error" in js:
        err = js.get("error", js)
        raise RuntimeError(f"Meta API 錯誤（{r.status_code}）：{err}")
    return js


def split_threads_text(text: str, limit: int = THREADS_LIMIT - 10) -> list[str]:
    """超過 500 字就以段落切成多則串文。"""
    if len(text) <= limit:
        return [text]
    chunks, cur = [], ""
    for para in text.split("\n"):
        candidate = f"{cur}\n{para}" if cur else para
        if len(candidate) <= limit:
            cur = candidate
            continue
        if cur:
            chunks.append(cur)
        while len(para) > limit:  # a single very long paragraph
            chunks.append(para[:limit])
            para = para[limit:]
        cur = para
    if cur:
        chunks.append(cur)
    n = len(chunks)
    return [f"{c.strip()}\n({i}/{n})" for i, c in enumerate(chunks, 1)]


# ---------------------------------------------------------------- Threads

class Threads:
    def __init__(self):
        self.token = env("THREADS_ACCESS_TOKEN")
        self.base = f"https://graph.threads.net/{_ver('threads_api_version', 'v1.0')}"

    def _wait(self, container_id: str, timeout: int = 180):
        start = time.time()
        while time.time() - start < timeout:
            js = _call("GET", f"{self.base}/{container_id}", fields="status,error_message", access_token=self.token)
            if js.get("status") == "FINISHED":
                return
            if js.get("status") in ("ERROR", "EXPIRED"):
                raise RuntimeError(f"Threads 處理失敗：{js}")
            time.sleep(5)
        raise RuntimeError("Threads 容器處理逾時")

    def _container(self, **params) -> str:
        cid = _call("POST", f"{self.base}/me/threads", access_token=self.token, **params)["id"]
        self._wait(cid)
        return cid

    def _publish(self, creation_id: str) -> str:
        return _call("POST", f"{self.base}/me/threads_publish", creation_id=creation_id, access_token=self.token)["id"]

    def post(self, text: str, image_urls: list[str]) -> str:
        parts = split_threads_text(text)
        first = parts[0]
        if len(image_urls) >= 2:
            children = [self._container(media_type="IMAGE", image_url=u, is_carousel_item="true") for u in image_urls[:20]]
            cid = self._container(media_type="CAROUSEL", children=",".join(children), text=first)
        elif len(image_urls) == 1:
            cid = self._container(media_type="IMAGE", image_url=image_urls[0], text=first)
        else:
            cid = self._container(media_type="TEXT", text=first)
        root_id = self._publish(cid)

        prev = root_id
        for part in parts[1:]:  # 其餘段落用「回覆自己」的方式串起來
            rid = self._container(media_type="TEXT", text=part, reply_to_id=prev)
            prev = self._publish(rid)

        js = _call("GET", f"{self.base}/{root_id}", fields="permalink", access_token=self.token)
        return js.get("permalink", root_id)

    def refresh_token(self) -> str:
        js = _call("GET", "https://graph.threads.net/refresh_access_token",
                   grant_type="th_refresh_token", access_token=self.token)
        return js["access_token"]


# ---------------------------------------------------------------- Instagram

class Instagram:
    def __init__(self):
        self.token = env("IG_ACCESS_TOKEN")
        self.base = f"https://graph.instagram.com/{_ver('instagram_api_version', 'v23.0')}"
        me = _call("GET", f"{self.base}/me", fields="user_id,username", access_token=self.token)
        self.user_id = me.get("user_id") or me["id"]

    def _wait(self, container_id: str, timeout: int = 300):
        start = time.time()
        while time.time() - start < timeout:
            js = _call("GET", f"{self.base}/{container_id}", fields="status_code", access_token=self.token)
            if js.get("status_code") == "FINISHED":
                return
            if js.get("status_code") in ("ERROR", "EXPIRED"):
                raise RuntimeError(f"IG 處理失敗：{js}")
            time.sleep(5)
        raise RuntimeError("IG 容器處理逾時")

    def _container(self, **params) -> str:
        cid = _call("POST", f"{self.base}/{self.user_id}/media", access_token=self.token, **params)["id"]
        self._wait(cid)
        return cid

    def post(self, caption: str, image_urls: list[str]) -> str:
        if not image_urls:
            raise RuntimeError("IG 貼文至少需要一張圖")
        if len(image_urls) == 1:
            cid = self._container(image_url=image_urls[0], caption=caption)
        else:
            children = [self._container(image_url=u, is_carousel_item="true") for u in image_urls[:10]]
            cid = self._container(media_type="CAROUSEL", children=",".join(children), caption=caption)
        media_id = _call("POST", f"{self.base}/{self.user_id}/media_publish", creation_id=cid, access_token=self.token)["id"]
        js = _call("GET", f"{self.base}/{media_id}", fields="permalink", access_token=self.token)
        return js.get("permalink", media_id)

    def refresh_token(self) -> str:
        js = _call("GET", "https://graph.instagram.com/refresh_access_token",
                   grant_type="ig_refresh_token", access_token=self.token)
        return js["access_token"]
