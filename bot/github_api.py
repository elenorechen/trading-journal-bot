"""最小化的 GitHub REST 呼叫：開草稿 issue、留言、加標籤、關閉。"""
from __future__ import annotations

import requests

from .settings import env

API = "https://api.github.com"

LABELS = {
    "draft": ("fbca04", "待審核的貼文草稿"),
    "published": ("0e8a16", "已發布"),
    "skipped": ("cccccc", "略過不發"),
    "plan": ("1d76db", "盤前規劃投稿"),
    "threads-done": ("000000", "已發到 Threads"),
    "ig-done": ("e4405f", "已發到 Instagram"),
}


def _h() -> dict:
    return {
        "Authorization": f"Bearer {env('GITHUB_TOKEN')}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def repo() -> str:
    return env("GITHUB_REPOSITORY")


def branch() -> str:
    return env("GITHUB_REF_NAME", required=False) or "main"


def raw_url(rel_path: str) -> str:
    return f"https://raw.githubusercontent.com/{repo()}/{branch()}/{rel_path}"


def _req(method: str, path: str, **kw):
    r = requests.request(method, f"{API}/repos/{repo()}{path}", headers=_h(), timeout=30, **kw)
    if r.status_code >= 400:
        raise RuntimeError(f"GitHub API {method} {path} → {r.status_code}: {r.text[:300]}")
    return r.json() if r.text else None


def ensure_labels():
    for name, (color, desc) in LABELS.items():
        r = requests.post(f"{API}/repos/{repo()}/labels", headers=_h(), timeout=30,
                          json={"name": name, "color": color, "description": desc})
        if r.status_code not in (201, 422):  # 422 = already exists
            raise RuntimeError(f"建立標籤失敗：{r.text[:200]}")


def create_issue(title: str, body: str, labels: list[str]) -> dict:
    ensure_labels()
    return _req("POST", "/issues", json={"title": title, "body": body, "labels": labels})


def get_issue(number: int) -> dict:
    return _req("GET", f"/issues/{number}")


def comment(number: int, body: str):
    return _req("POST", f"/issues/{number}/comments", json={"body": body})


def close(number: int, labels: list[str] | None = None):
    payload: dict = {"state": "closed"}
    if labels is not None:
        payload["labels"] = labels
    return _req("PATCH", f"/issues/{number}", json=payload)


def set_labels(number: int, labels: list[str]):
    return _req("PATCH", f"/issues/{number}", json={"labels": labels})


def download(url: str) -> bytes:
    """下載 issue 裡貼的圖片附件（user-attachments）。"""
    r = requests.get(url, headers={"Authorization": f"Bearer {env('GITHUB_TOKEN')}"}, timeout=60, allow_redirects=True)
    if r.status_code in (401, 403, 404):  # some attachment hosts reject the token; retry anonymously
        r = requests.get(url, timeout=60, allow_redirects=True)
    r.raise_for_status()
    return r.content
