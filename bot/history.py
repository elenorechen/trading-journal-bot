"""每日關鍵數字存在 data/history.json，用來算「比前一日增減」與畫趨勢。"""
from __future__ import annotations

import json

from .settings import DATA_DIR

PATH = DATA_DIR / "history.json"


def load() -> dict:
    if PATH.exists():
        return json.loads(PATH.read_text(encoding="utf-8"))
    return {}


def save(hist: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    keep = dict(sorted(hist.items())[-250:])  # 約一年的交易日
    PATH.write_text(json.dumps(keep, ensure_ascii=False, indent=1), encoding="utf-8")


def snapshot(tw: dict) -> dict:
    """從籌碼資料抽出要長期追蹤的幾個數字。"""
    def dig(*path):
        cur = tw
        for p in path:
            if cur is None:
                return None
            cur = cur.get(p)
        return cur

    return {
        "foreign_tx_oi": dig("fut_inst", "tx", "foreign", "oi_net"),
        "trust_tx_oi": dig("fut_inst", "tx", "trust", "oi_net"),
        "dealer_tx_oi": dig("fut_inst", "tx", "dealer", "oi_net"),
        "top10_net": dig("large", "top10_net"),
        "retail_ratio": tw.get("retail_ratio"),
        "pcr_oi": dig("pcr", "oi_ratio"),
        "taiex": dig("taiex", "close"),
        "foreign_stock": dig("stock_inst", "foreign"),
    }


def previous(hist: dict, date: str) -> dict | None:
    earlier = [d for d in hist if d < date]
    return hist[max(earlier)] if earlier else None


def diff(today: dict, prev: dict | None, key: str):
    if not prev or today.get(key) is None or prev.get(key) is None:
        return None
    return round(today[key] - prev[key], 2)


def series(hist: dict, key: str, n: int = 20) -> list[tuple[str, float]]:
    pts = [(d, v[key]) for d, v in sorted(hist.items()) if v.get(key) is not None]
    return pts[-n:]
