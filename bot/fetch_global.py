"""國際盤（美股、美債、美元、VIX）與總經新聞標題。"""
from __future__ import annotations

import html
import math
import re
import time

from .net import get_json

# 顯示名稱 → Yahoo Finance 代號
US_TICKERS = {
    "道瓊": "^DJI",
    "S&P 500": "^GSPC",
    "那斯達克": "^IXIC",
    "費城半導體": "^SOX",
    "台積電 ADR": "TSM",
    "S&P 期貨": "ES=F",
    "那指期貨": "NQ=F",
    "VIX 恐慌指數": "^VIX",
    "美債 10 年殖利率": "^TNX",
    "美元指數": "DX-Y.NYB",
    "美元/台幣": "USDTWD=X",
}

NEWS_CATEGORIES = ["wd_macro", "tw_macro", "tw_stock", "us_stock"]


def us_markets() -> list[dict]:
    import yfinance as yf

    df = yf.download(
        list(US_TICKERS.values()), period="7d", interval="1d",
        progress=False, auto_adjust=False, threads=False,
    )["Close"]
    out = []
    for name, sym in US_TICKERS.items():
        if sym not in df:
            continue
        series = [v for v in df[sym].tolist() if v is not None and not math.isnan(v)]
        if len(series) < 2:
            continue
        last, prev = series[-1], series[-2]
        is_yield = sym == "^TNX"
        out.append({
            "name": name,
            "symbol": sym,
            "close": round(last, 3 if is_yield or sym == "USDTWD=X" else 2),
            # 殖利率用「基點」變化比較直覺
            "change": round((last - prev) * 100, 1) if is_yield else round(last - prev, 2),
            "change_pct": round((last - prev) / prev * 100, 2),
            "unit": "bp" if is_yield else "",
        })
    return out


def _clean(s: str) -> str:
    s = html.unescape(s or "")
    s = re.sub(r"<[^>]+>", "", s)
    return re.sub(r"\s+", " ", s).strip()


def news_headlines(hours: int = 18, per_category: int = 30) -> list[dict]:
    """鉅亨網新聞（總經、台股、美股），只取最近 N 小時。"""
    cutoff = time.time() - hours * 3600
    seen, out = set(), []
    for cat in NEWS_CATEGORIES:
        try:
            js = get_json(f"https://api.cnyes.com/media/api/v1/newslist/category/{cat}", params={"limit": per_category})
        except Exception:
            continue
        for item in js.get("items", {}).get("data", []):
            nid = item.get("newsId")
            if nid in seen or item.get("publishAt", 0) < cutoff:
                continue
            seen.add(nid)
            out.append({
                "category": cat,
                "title": _clean(item.get("title", "")),
                "summary": _clean(item.get("summary", ""))[:160],
                "published": time.strftime("%m/%d %H:%M", time.gmtime(item["publishAt"] + 8 * 3600)),
                "url": f"https://news.cnyes.com/news/id/{nid}",
            })
    out.sort(key=lambda x: x["published"], reverse=True)
    return out


def collect() -> dict:
    data: dict = {"errors": []}
    try:
        data["us"] = us_markets()
    except Exception as e:
        data["us"] = []
        data["errors"].append(f"us_markets: {e}")
    try:
        data["news"] = news_headlines()
    except Exception as e:
        data["news"] = []
        data["errors"].append(f"news: {e}")
    return data
