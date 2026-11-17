"""台股與台指期籌碼資料（證交所 TWSE、期交所 TAIFEX 官方開放資料）。

所有金額統一轉成「億元」，期貨部位單位是「口」。
"""
from __future__ import annotations

import csv
import io
import json
from datetime import date, datetime
from functools import lru_cache

from .net import get_json, get_text

TWSE = "https://www.twse.com.tw/rwd/zh"
TAIFEX = "https://openapi.taifex.com.tw/v1"


def _num(s) -> float:
    s = str(s).replace(",", "").replace("+", "").strip()
    if s in ("", "-", "--", "NULL", "X"):
        raise ValueError(f"not a number: {s!r}")
    return float(s)


def _yi(thousand_or_yuan: float, unit: str) -> float:
    """Convert to 億元."""
    return round(thousand_or_yuan / (1e5 if unit == "thousand" else 1e8), 2)


# ---------------------------------------------------------------- TAIFEX
# 期交所 OpenAPI 會依伺服器節點隨機回傳 JSON 或 CSV（同一個網址、同樣的請求），
# 兩種格式欄位順序相同，所以統一轉成「一列 = 字串清單」再依欄位位置讀取。

@lru_cache(maxsize=None)
def taifex_rows(endpoint: str) -> tuple[tuple[str, ...], ...]:
    text = get_text(f"{TAIFEX}/{endpoint}").lstrip("﻿").strip()
    if text.startswith("["):
        rows = [tuple(str(v).strip() for v in obj.values()) for obj in json.loads(text)]
    else:
        rows = [tuple(c.strip() for c in r) for r in csv.reader(io.StringIO(text))][1:]  # 去掉中文表頭
    rows = [r for r in rows if r and r[0][:1].isdigit()]
    if not rows:
        raise RuntimeError(f"期交所 {endpoint} 沒有資料")
    return tuple(rows)


FUT_INST = "MarketDataOfMajorInstitutionalTradersDetailsOfFuturesContractsBytheDate"


def _who(item: str) -> str | None:
    for prefix, key in (("外資", "foreign"), ("投信", "trust"), ("自營", "dealer")):
        if item.startswith(prefix):
            return key
    return None


def latest_trading_date() -> str:
    """期交所最新一筆三大法人資料的日期 (YYYYMMDD)，用來當作「資料日」。"""
    return taifex_rows(FUT_INST)[0][0]


def futures_institutional() -> dict:
    """三大法人台指期 / 小台未平倉淨口數。
    欄位：0 日期, 1 商品, 2 身份別, 7 交易口數淨額, 13 未平倉口數淨額"""
    rows = taifex_rows(FUT_INST)
    out: dict = {"date": rows[0][0], "tx": {}, "mtx": {}}
    for r in rows:
        key = {"臺股期貨": "tx", "小型臺指期貨": "mtx"}.get(r[1])
        who = _who(r[2])
        if key and who:
            out[key][who] = {"oi_net": int(_num(r[13])), "trade_net": int(_num(r[7]))}
    if not out["tx"]:
        raise RuntimeError("找不到台指期三大法人資料")
    return out


def futures_market() -> dict:
    """台指期近月結算價 / 漲跌，以及小台全市場未平倉（算散戶多空比用）。
    欄位：0 日期, 1 契約, 2 月份, 6 最後價, 7 漲跌, 8 %, 9 量, 10 結算價, 11 未平倉, 17 交易時段"""
    rows = taifex_rows("DailyMarketReportFut")
    day = [r for r in rows if len(r) > 17 and r[17] == "一般"]
    if not day:  # 時段欄位格式不同時：一般盤才有結算價
        day = [r for r in rows if len(r) > 11 and r[10] not in ("NULL", "-", "")]
    tx = sorted((r for r in day if r[1] == "TX" and r[2].isdigit()), key=lambda r: r[2])
    if not tx:
        raise RuntimeError("找不到台指期行情")
    near = tx[0]
    mtx_oi = 0
    for r in day:
        if r[1] == "MTX":
            try:
                mtx_oi += int(_num(r[11]))
            except ValueError:
                pass
    try:
        close = _num(near[10])
    except ValueError:
        close = _num(near[6])
    return {
        "date": near[0],
        "tx_month": near[2],
        "tx_close": close,
        "tx_change": _num(near[7]),
        "tx_change_pct": near[8],
        "tx_volume": int(_num(near[9])),
        "mtx_total_oi": mtx_oi,
    }


def put_call_ratio() -> dict:
    """欄位：0 日期, 3 成交量比率%, 6 未平倉比率%"""
    r = max(taifex_rows("PutCallRatio"), key=lambda r: r[0])
    return {"date": r[0], "oi_ratio": _num(r[6]), "volume_ratio": _num(r[3])}


def large_traders() -> dict:
    """台指期（TX+MTX/4）所有月份，前五 / 前十大交易人淨部位。
    欄位：0 日期, 1 契約, 3 到期月份, 4 交易人類別(0=全部), 5-8 前五/前十 買賣, 9 全市場未沖銷"""
    for row in taifex_rows("OpenInterestOfLargeTradersFutures"):
        if len(row) >= 10 and row[1] == "TX" and row[3] == "999912" and row[4] == "0":
            b5, s5, b10, s10, total = (int(_num(x)) for x in row[5:10])
            return {"date": row[0], "top5_net": b5 - s5, "top10_net": b10 - s10, "market_oi": total}
    raise RuntimeError("找不到台指期大額交易人資料")


# ---------------------------------------------------------------- TWSE

def stock_institutional(d: str) -> dict:
    """現貨三大法人買賣超（億元）。d = YYYYMMDD"""
    js = get_json(f"{TWSE}/fund/BFI82U", params={"type": "day", "dayDate": d, "response": "json"})
    if js.get("stat") != "OK":
        raise RuntimeError(f"證交所三大法人資料尚未公布：{js.get('stat')}")
    vals = {row[0]: _num(row[3]) for row in js["data"]}
    dealer = vals.get("自營商(自行買賣)", 0) + vals.get("自營商(避險)", 0)
    return {
        "foreign": _yi(vals.get("外資及陸資(不含外資自營商)", 0), "yuan"),
        "trust": _yi(vals.get("投信", 0), "yuan"),
        "dealer": _yi(dealer, "yuan"),
        "total": _yi(vals.get("合計", 0), "yuan"),
    }


def margin(d: str) -> dict:
    """融資餘額（億元）、融券餘額（張）與增減。"""
    js = get_json(f"{TWSE}/marginTrading/MI_MARGN", params={"date": d, "selectType": "MS", "response": "json"})
    if js.get("stat") != "OK":
        raise RuntimeError(f"證交所融資融券資料尚未公布：{js.get('stat')}")
    rows = {r[0]: r for r in js["tables"][0]["data"]}
    m = rows["融資金額(仟元)"]
    s = rows["融券(交易單位)"]
    return {
        "margin_balance": _yi(_num(m[5]), "thousand"),
        "margin_change": _yi(_num(m[5]) - _num(m[4]), "thousand"),
        "short_balance": int(_num(s[5])),
        "short_change": int(_num(s[5]) - _num(s[4])),
    }


def taiex(d: str) -> dict:
    """加權指數收盤、漲跌與成交金額。"""
    js = get_json(f"{TWSE}/afterTrading/FMTQIK", params={"date": d, "response": "json"})
    dt = datetime.strptime(d, "%Y%m%d")
    roc = f"{dt.year - 1911}/{dt.month:02d}/{dt.day:02d}"
    for row in js.get("data", []):
        if row[0] == roc:
            close, chg = _num(row[4]), _num(row[5])
            return {
                "close": close,
                "change": chg,
                "change_pct": round(chg / (close - chg) * 100, 2),
                "turnover": _yi(_num(row[2]), "yuan"),
            }
    raise RuntimeError(f"找不到 {d} 的加權指數資料")


def twse_latest_date() -> str:
    """證交所本月最後一個有資料的交易日（期交所連不上時的備援）。"""
    js = get_json(f"{TWSE}/afterTrading/FMTQIK", params={"response": "json"})
    y, m, d = js["data"][-1][0].split("/")
    return f"{int(y) + 1911}{m}{d}"


def holidays(year: int) -> set[str]:
    """證交所休市日 (YYYY-MM-DD)。「開始交易日 / 最後交易日」那種列不是休市，要排除。"""
    js = get_json(f"{TWSE}/holidaySchedule/holidaySchedule", params={"date": f"{year}0101", "response": "json"})
    return {r[0] for r in js.get("data", []) if "交易" not in r[1]}


def is_market_closed(today: date) -> bool:
    if today.weekday() >= 5:
        return True
    try:
        return today.isoformat() in holidays(today.year)
    except Exception:
        return False  # 查不到就照常產生，交給你審核時決定


# ---------------------------------------------------------------- all-in-one

def collect() -> dict:
    """抓齊所有台股籌碼資料。單一來源失敗不會讓整份資料失敗，會記在 errors 裡。"""
    data: dict = {"errors": []}
    try:
        data_date = latest_trading_date()
    except Exception as e:
        data["errors"].append(f"taifex_date: {e}")
        data_date = twse_latest_date()
    data["date"] = data_date

    jobs = {
        "stock_inst": lambda: stock_institutional(data_date),
        "margin": lambda: margin(data_date),
        "taiex": lambda: taiex(data_date),
        "fut_inst": futures_institutional,
        "fut_market": futures_market,
        "pcr": put_call_ratio,
        "large": large_traders,
    }
    for key, fn in jobs.items():
        try:
            data[key] = fn()
        except Exception as e:  # keep going; the draft shows what's missing
            data[key] = None
            data["errors"].append(f"{key}: {e}")

    # 散戶多空比（小台）= -(三大法人小台淨未平倉) / 小台全市場未平倉
    try:
        inst_net = sum(v["oi_net"] for v in data["fut_inst"]["mtx"].values())
        total = data["fut_market"]["mtx_total_oi"]
        data["retail_ratio"] = round(-inst_net / total * 100, 2) if total else None
    except Exception:
        data["retail_ratio"] = None
    return data
