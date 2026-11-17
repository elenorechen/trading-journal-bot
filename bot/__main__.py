"""指令入口。

  python -m bot daily              抓資料、寫文、畫圖卡，存成草稿資料夾（本機也能跑，用來預覽）
  python -m bot open-draft <資料夾>  在 GitHub 開一個草稿 issue 給你審核
  python -m bot plan --issue N     把你投稿的「盤前規劃」issue 轉成草稿
  python -m bot command --issue N  處理 /publish、/skip 留言
  python -m bot refresh-tokens     展延 IG / Threads 權杖（每週自動跑）
"""
from __future__ import annotations

import argparse
import io
import logging
import os
import re
import shutil
import subprocess
import sys

from . import cards, drafts, history
from .settings import CONFIG, POSTS_DIR, ROOT, env, now_tw

logging.getLogger("yfinance").setLevel(logging.CRITICAL)


def gh_output(key: str, value: str):
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{key}={value}\n")


def rel(p) -> str:
    return p.relative_to(ROOT).as_posix()


# ---------------------------------------------------------------- daily

def cmd_daily(args):
    from . import fetch_global, fetch_tw, writer

    today = now_tw().date()
    if not args.force and fetch_tw.is_market_closed(today):
        print(f"{today} 台股休市，今天不產生草稿（要強制產生請加 --force）")
        gh_output("skip", "true")
        return

    print("📥 抓取台股籌碼…")
    tw = fetch_tw.collect()
    print("📥 抓取國際盤與新聞…")
    glob = fetch_global.collect()

    hist = history.load()
    snap = history.snapshot(tw)
    prev = history.previous(hist, tw["date"])
    diffs = {k: history.diff(snap, prev, k) for k in snap}
    hist[tw["date"]] = snap
    history.save(hist)

    label = cards.date_label(today)
    print("✍️ 撰寫貼文…")
    text = writer.write(tw, glob, diffs, label)
    points = text["macro_points"] or [n["title"] for n in glob["news"] if n["category"].endswith("macro")][:4]

    folder = POSTS_DIR / today.isoformat() / "premarket"
    folder.mkdir(parents=True, exist_ok=True)
    print("🎨 產生圖卡…")
    images = ["1-chips.jpg", "2-global.jpg", "3-macro.jpg"]
    cards.chips_card(tw, diffs, today, folder / images[0])
    if glob["us"]:
        cards.global_card(glob["us"], today, folder / images[1])
    else:
        images.remove("2-global.jpg")
    cards.macro_card(points, text["events"], history.series(hist, "foreign_tx_oi"), today, folder / images[-1])

    drafts.save(
        folder,
        title=f"📋 盤前籌碼與總經 {label}",
        images=images,
        threads=drafts.with_footer_threads(text["threads"]),
        ig=drafts.with_footer_ig(text["ig"]),
        extra={"kind": "premarket", "data_date": tw["date"], "errors": tw["errors"] + glob["errors"]},
    )
    print(f"✅ 草稿完成：{rel(folder)}")
    print("\n----- Threads -----\n" + (folder / "threads.txt").read_text(encoding="utf-8"))
    print("\n----- IG -----\n" + (folder / "ig.txt").read_text(encoding="utf-8"))
    gh_output("dir", rel(folder))
    gh_output("skip", "false")


# ---------------------------------------------------------------- open draft issue

def cmd_open_draft(args):
    from . import github_api as gh

    rel_dir = args.dir.strip("/")
    meta = drafts.load_meta(rel_dir)
    threads = (ROOT / rel_dir / "threads.txt").read_text(encoding="utf-8")
    ig = (ROOT / rel_dir / "ig.txt").read_text(encoding="utf-8")
    urls = [gh.raw_url(f"{rel_dir}/{img}") for img in meta["images"]]
    body = drafts.issue_body(rel_dir, urls, threads, ig, meta.get("errors", []))
    issue = gh.create_issue(meta["title"], body, ["draft"])
    print(f"✅ 已開草稿 issue #{issue['number']}：{issue['html_url']}")
    if args.source_issue:
        gh.comment(args.source_issue, f"已轉成草稿 → #{issue['number']}，確認後在那邊留言 `/publish`。")
        gh.close(args.source_issue)


# ---------------------------------------------------------------- plan submission

def _form_sections(body: str) -> dict:
    out, cur = {}, None
    for line in (body or "").splitlines():
        m = re.match(r"^###\s+(.+?)\s*$", line)
        if m:
            cur = m.group(1)
            out[cur] = []
        elif cur:
            out[cur].append(line)
    return {k: "\n".join(v).strip() for k, v in out.items()}


def cmd_plan(args):
    from PIL import Image

    from . import github_api as gh

    issue = gh.get_issue(args.issue)
    sec = _form_sections(issue["body"])
    title = sec.get("標題", "").replace("_No response_", "").strip() or issue["title"]
    plan_text = sec.get("盤前規劃內容", "").replace("_No response_", "").strip()
    chart_md = sec.get("TradingView 圖表", "")
    urls = re.findall(r"!\[[^\]]*\]\((https?://[^)\s]+)\)", chart_md) + re.findall(r'<img[^>]+src="([^"]+)"', chart_md)
    attach_chips = "[x]" in sec.get("選項", "").lower()

    today = now_tw().date()
    folder = POSTS_DIR / today.isoformat() / f"plan-{args.issue}"
    folder.mkdir(parents=True, exist_ok=True)
    images = []
    for i, u in enumerate(urls[:9], 1):
        img = Image.open(io.BytesIO(gh.download(u)))
        name = f"{i}-chart.jpg"
        page = f"｜ {i}/{len(urls[:9])}" if len(urls) > 1 else ""
        cards.plan_card(img, title, today, folder / name, page)
        images.append(name)

    chips = POSTS_DIR / today.isoformat() / "premarket" / "1-chips.jpg"
    if attach_chips and chips.exists():
        shutil.copy(chips, folder / "9-chips.jpg")
        images.append("9-chips.jpg")
    if not images:
        gh.comment(args.issue, "⚠️ 沒有找到圖片。IG 貼文一定要有圖，請在「TradingView 圖表」欄位貼上截圖後再開一次。")
        raise SystemExit("plan issue 沒有圖片")

    body = f"{title}\n\n{plan_text}".strip()
    drafts.save(
        folder,
        title=f"📈 盤前規劃 {cards.date_label(today)}｜{title}",
        images=images,
        threads=drafts.with_footer_threads(body),
        ig=drafts.with_footer_ig(body),
        extra={"kind": "plan", "source_issue": args.issue, "errors": []},
    )
    print(f"✅ 盤前規劃草稿：{rel(folder)}")
    gh_output("dir", rel(folder))


# ---------------------------------------------------------------- /publish /skip

def cmd_command(args):
    from . import github_api as gh
    from .meta_api import Instagram, Threads

    comment = os.environ.get("COMMENT", "").strip().split()
    if not comment:
        return
    issue = gh.get_issue(args.issue)
    labels = {l["name"] for l in issue["labels"]}
    if "draft" not in labels:
        print("不是草稿 issue，略過")
        return

    if comment[0] == "/skip":
        gh.close(args.issue, labels=sorted((labels - {"draft"}) | {"skipped"}))
        gh.comment(args.issue, "👌 這篇不發，已關閉。")
        return
    if comment[0] != "/publish":
        return

    target = comment[1].lower() if len(comment) > 1 else "all"
    want = {"threads", "ig"} if target == "all" else {target}
    if not want <= {"threads", "ig"}:
        gh.comment(args.issue, f"看不懂 `{target}`，請用 `/publish`、`/publish threads` 或 `/publish ig`。")
        return

    parsed = drafts.parse_issue(issue["body"])
    meta = drafts.load_meta(parsed["dir"])
    urls = [gh.raw_url(f"{parsed['dir']}/{img}") for img in meta["images"]]

    results, failed = [], False
    for platform in sorted(want):
        if f"{platform}-done" in labels:
            results.append(f"- {platform}：之前已經發過了，略過")
            continue
        try:
            if platform == "threads":
                link = Threads().post(parsed["threads"], urls)
            else:
                link = Instagram().post(parsed["ig"], urls)
            labels.add(f"{platform}-done")
            results.append(f"- ✅ {'Threads' if platform == 'threads' else 'Instagram'}：{link}")
        except Exception as e:
            failed = True
            results.append(f"- ❌ {platform} 發文失敗：`{e}`")

    gh.comment(args.issue, "**發布結果**\n" + "\n".join(results))
    if {"threads-done", "ig-done"} <= labels:
        gh.close(args.issue, labels=sorted((labels - {"draft"}) | {"published"}))
    else:
        gh.set_labels(args.issue, sorted(labels))
    if failed:
        sys.exit(1)


# ---------------------------------------------------------------- token refresh

def cmd_refresh_tokens(args):
    from .meta_api import Instagram, Threads

    pat = env("GH_PAT", required=False)
    if not pat:
        raise SystemExit("沒有設定 GH_PAT，無法自動更新 Secrets。請看 README 的「權杖自動展延」。")
    updates = {}
    for name, cls in (("THREADS_ACCESS_TOKEN", Threads), ("IG_ACCESS_TOKEN", Instagram)):
        if not os.environ.get(name):
            continue
        try:
            updates[name] = cls().refresh_token()
        except Exception as e:
            print(f"⚠️ {name} 展延失敗：{e}")
    for name, value in updates.items():
        print(f"::add-mask::{value}")
        subprocess.run(
            ["gh", "secret", "set", name, "--repo", env("GITHUB_REPOSITORY"), "--body", value],
            check=True, env={**os.environ, "GH_TOKEN": pat},
        )
        print(f"✅ 已更新 {name}")
    if len(updates) < sum(1 for n in ("THREADS_ACCESS_TOKEN", "IG_ACCESS_TOKEN") if os.environ.get(n)):
        sys.exit(1)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(prog="bot")
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("daily")
    d.add_argument("--force", action="store_true", help="休市日也產生")
    d.set_defaults(fn=cmd_daily)

    o = sub.add_parser("open-draft")
    o.add_argument("dir")
    o.add_argument("--source-issue", type=int)
    o.set_defaults(fn=cmd_open_draft)

    pl = sub.add_parser("plan")
    pl.add_argument("--issue", type=int, required=True)
    pl.set_defaults(fn=cmd_plan)

    c = sub.add_parser("command")
    c.add_argument("--issue", type=int, required=True)
    c.set_defaults(fn=cmd_command)

    r = sub.add_parser("refresh-tokens")
    r.set_defaults(fn=cmd_refresh_tokens)

    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
