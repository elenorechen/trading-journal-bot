"""台股與台指期籌碼資料（證交所 TWSE、期交所 TAIFEX 官方開放資料）。

所有金額統一轉成「億元」，期貨部位單位是「口」。
"""
from __future__ import annotations

import csv
import io
from datetime import date, datetime

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

def latest_trading_date() -> str:
    """期交所最新一筆三大法人資料的日期 (YYYYMMDD)，用來當作「資料日」。"""
    rows = get_json(f"{TAIFEX}/MarketDataOfMajorInstitutionalTradersDetailsOfFuturesContractsBytheDate")
    return rows[0]["Date"]


def futures_institutional() -> dict:
    """三大法人台指期 / 小台未平倉淨口數。"""
    rows = get_json(f"{TAIFEX}/MarketDataOfMajorInstitutionalTradersDetailsOfFuturesContractsBytheDate")
    names = {"外資及陸資": "foreign", "投信": "trust", "自營商": "dealer"}
    out: dict = {"date": rows[0]["Date"], "tx": {}, "mtx": {}}
    for r in rows:
        key = {"臺股期貨": "tx", "小型臺指期貨": "mtx"}.get(r["ContractCode"])
        who = names.get(r["Item"])
        if key and who:
            out[key][who] = {
                "oi_net": int(_num(r["OpenInterest(Net)"])),
                "trade_net": int(_num(r["TradingVolume(Net)"])),
            }
    return out


def futures_market() -> dict:
    """台指期近月結算價 / 漲跌，以及小台全市場未平倉（算散戶多空比用）。"""
    rows = get_json(f"{TAIFEX}/DailyMarketReportFut")
    day = [r for r in rows if r["TradingSession"] == "一般"]
    tx = sorted(
        (r for r in day if r["Contract"] == "TX" and r["ContractMonth(Week)"].isdigit()),
        key=lambda r: r["ContractMonth(Week)"],
    )
    near = tx[0]
    mtx_oi = 0
    for r in day:
        if r["Contract"] == "MTX":
            try:
                mtx_oi += int(_num(r["OpenInterest"]))
            except ValueError:
                pass
    return {
        "date": near["Date"],
        "tx_month": near["ContractMonth(Week)"],
        "tx_close": _num(near["SettlementPrice"]) if near["SettlementPrice"] not in ("NULL", "-") else _num(near["Last"]),
        "tx_change": _num(near["Change"]),
        "tx_change_pct": near["%"],
        "tx_volume": int(_num(near["Volume"])),
        "mtx_total_oi": mtx_oi,
    }


def put_call_ratio() -> dict:
    rows = get_json(f"{TAIFEX}/PutCallRatio")
    r = rows[0]
    return {"date": r["Date"], "oi_ratio": _num(r["PutCallOIRatio%"]), "volume_ratio": _num(r["PutCallVolumeRatio%"])}


def large_traders() -> dict:
    """台指期（TX+MTX/4）所有月份，前五 / 前十大交易人淨部位。"""
    text = get_text(f"{TAIFEX}/OpenInterestOfLargeTradersFutures")
    for row in csv.reader(io.StringIO(text)):
        # 日期,契約,商品名稱,到期月份,交易人類別(0=全部 1=特定法人),前五買,前五賣,前十買,前十賣,全市場未沖銷
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
