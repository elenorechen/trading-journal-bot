"""草稿的存放與 issue 內文格式。

每份草稿是 posts/<日期>/<種類>/ 底下的一個資料夾：
  meta.json   圖片清單、標題
  threads.txt Threads 文字
  ig.txt      IG 文字
  *.jpg       圖卡

issue 內文會放可編輯的文字區塊；發布時以「issue 裡的文字」為準，
所以你可以直接在 GitHub 上改字再留言 /publish。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .settings import CONFIG, ROOT

MARK = "<!-- draft-dir: {} -->"
FENCE = "~~~~"


def save(folder: Path, *, title: str, images: list[str], threads: str, ig: str, extra: dict | None = None):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "threads.txt").write_text(threads, encoding="utf-8")
    (folder / "ig.txt").write_text(ig, encoding="utf-8")
    meta = {"title": title, "images": images, **(extra or {})}
    (folder / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")


def load_meta(rel_dir: str) -> dict:
    return json.loads((ROOT / rel_dir / "meta.json").read_text(encoding="utf-8"))


def with_footer_ig(text: str) -> str:
    b = CONFIG["brand"]
    parts = [text.strip(), ""]
    if b.get("support_link"):
        parts.append(f"☕ 覺得有幫助可以支持我：{b['support_link']}（連結在個人檔案）")
    parts.append(f"⚠️ {b['disclaimer']}")
    parts.append("")
    parts.append(" ".join(f"#{t}" for t in b.get("hashtags", [])))
    return "\n".join(parts).strip()


def with_footer_threads(text: str) -> str:
    return text.strip() + "\n\n⚠️ 個人紀錄，非投資建議"


def issue_body(rel_dir: str, image_urls: list[str], threads: str, ig: str, notes: list[str]) -> str:
    imgs = "\n".join(f'<img src="{u}" width="260">' for u in image_urls)
    warn = ""
    if notes:
        warn = "> [!WARNING]\n> 部分資料抓取失敗，請確認內容：\n" + "\n".join(f"> - {n}" for n in notes) + "\n\n"
    return f"""{MARK.format(rel_dir)}
{warn}### 圖卡預覽
{imgs}

### Threads 文字（{len(threads)} 字，超過 500 字會自動拆成串文）
可以按右上角「…→ Edit」直接修改下面區塊裡的文字。
<!-- THREADS -->
{FENCE}
{threads}
{FENCE}
<!-- /THREADS -->

### IG 文字
<!-- IG -->
{FENCE}
{ig}
{FENCE}
<!-- /IG -->

---
**要怎麼做？** 在下面留言：
- `/publish`：同時發到 IG 和 Threads
- `/publish threads`：只發 Threads
- `/publish ig`：只發 IG
- `/skip`：這篇不發
"""


def _block(body: str, tag: str) -> str | None:
    m = re.search(rf"<!-- {tag} -->\s*(.*?)\s*<!-- /{tag} -->", body, flags=re.S)
    if not m:
        return None
    inner = m.group(1).strip()
    inner = re.sub(rf"^{FENCE}\w*\s*\n?", "", inner)
    inner = re.sub(rf"\n?{FENCE}\s*$", "", inner)
    return inner.strip().replace("\r\n", "\n")


def parse_issue(body: str) -> dict:
    m = re.search(r"<!-- draft-dir: (.+?) -->", body or "")
    if not m:
        raise SystemExit("這個 issue 不是機器人產生的草稿（找不到 draft-dir 標記）")
    return {"dir": m.group(1).strip(), "threads": _block(body, "THREADS"), "ig": _block(body, "IG")}
