"""用 Claude 把當天資料整理成貼文。沒有 API key 時退回固定模板。"""
from __future__ import annotations

import json
import os
import re

from .settings import CONFIG

SYSTEM = """你是一位台灣交易者的「交易日誌」小編，幫他把每天盤前要看的籌碼與總經資訊整理成社群貼文。

硬性規則：
1. 一律使用台灣繁體中文與台灣慣用語（例如：外資、投信、未平倉、口、億、殖利率）。
2. 數字只能來自使用者提供的資料或你用 web_search 查到並確認過的來源，不可以自己編造或推估數字。查不到就不寫。
3. 這是個人交易紀錄，不是投顧建議：不可以出現「建議買進/賣出」「目標價」「必漲/必跌」「跟單」等字眼，也不可以點名推薦個股。可以描述籌碼的偏多/偏空「結構」與需要留意的風險。
4. 台股習慣：紅色代表上漲、綠色代表下跌。描述時用「上漲/下跌」「增/減」即可。
5. 不要使用 Markdown 粗體或標題語法，社群平台不會渲染；可以用少量 emoji 當段落符號。
"""

TASK = """以下是 {date_label} 盤前要用的資料（籌碼為 {data_date} 收盤後公布的數據）。

【台股籌碼】
{tw}

【與前一交易日相比】
{diff}

【國際盤（美股昨夜收盤）】
{us}

【鉅亨網最近 18 小時新聞標題】
{news}

{search_hint}

請依照下面的格式輸出，每個區塊都要有，標記行要原樣保留：

[MACRO_POINTS]
3～5 行，每行一個總經/國際重點，每行 30 字以內，開頭不要加符號。
[EVENTS]
今天（台灣時間）與本週需要留意的經濟數據或事件，2～5 行，格式「時間｜事件」，時間用台灣時間。查不到就寫「今日無重大數據」。
[THREADS]
給 Threads 的貼文，全文 450 字以內。第一行是吸睛但不浮誇的標題。內容：昨夜國際盤一句話總結 → 籌碼重點（外資現貨、外資台指期未平倉、散戶多空比等）→ 今天要留意的事件 → 一句開放式提問邀請大家留言討論。
[IG]
給 Instagram 的貼文說明，800 字以內。比 Threads 完整一點，分段清楚，同樣以邀請留言討論作結。不要放 hashtag，系統會自動加。

寫作風格：{voice}
"""


def _fmt(v, unit="", signed=True):
    if v is None:
        return "—"
    s = f"{v:+,.2f}" if signed else f"{v:,.2f}"
    s = s.rstrip("0").rstrip(".") if "." in s else s
    return f"{s}{unit}"


def summarize_tw(tw: dict) -> str:
    lines = []
    if t := tw.get("taiex"):
        lines.append(f"加權指數 {t['close']:,.2f}（{_fmt(t['change'])} 點，{_fmt(t['change_pct'], '%')}），成交金額 {t['turnover']:,.0f} 億")
    if s := tw.get("stock_inst"):
        lines.append(f"現貨三大法人：外資 {_fmt(s['foreign'], ' 億')}、投信 {_fmt(s['trust'], ' 億')}、自營商 {_fmt(s['dealer'], ' 億')}，合計 {_fmt(s['total'], ' 億')}")
    if f := tw.get("fut_inst"):
        tx = f["tx"]
        lines.append(
            "台指期三大法人淨未平倉：外資 {:+,} 口、投信 {:+,} 口、自營商 {:+,} 口".format(
                tx["foreign"]["oi_net"], tx["trust"]["oi_net"], tx["dealer"]["oi_net"]
            )
        )
    if m := tw.get("fut_market"):
        lines.append(f"台指期近月（{m['tx_month']}）結算 {m['tx_close']:,.0f}（{_fmt(m['tx_change'])} 點）")
    if lg := tw.get("large"):
        lines.append(f"台指期前五大交易人淨部位 {lg['top5_net']:+,} 口、前十大 {lg['top10_net']:+,} 口")
    if tw.get("retail_ratio") is not None:
        lines.append(f"小台散戶多空比 {tw['retail_ratio']:+.2f}%（正值=散戶偏多）")
    if p := tw.get("pcr"):
        lines.append(f"選擇權 Put/Call 未平倉比 {p['oi_ratio']:.2f}%")
    if mg := tw.get("margin"):
        lines.append(f"融資餘額 {mg['margin_balance']:,.0f} 億（{_fmt(mg['margin_change'], ' 億')}），融券餘額 {mg['short_balance']:,} 張（{mg['short_change']:+,} 張）")
    return "\n".join(lines) or "（籌碼資料暫缺）"


def summarize_us(us: list[dict]) -> str:
    return "\n".join(
        f"{u['name']} {u['close']:,}（{_fmt(u['change'], u['unit'])}，{_fmt(u['change_pct'], '%')}）" for u in us
    ) or "（國際盤資料暫缺）"


def _parse_sections(text: str) -> dict:
    out = {}
    parts = re.split(r"^\[(MACRO_POINTS|EVENTS|THREADS|IG)\]\s*$", text, flags=re.M)
    for i in range(1, len(parts) - 1, 2):
        out[parts[i]] = parts[i + 1].strip()
    return out


def _lines(block: str) -> list[str]:
    return [re.sub(r"^\s*(?:[-•*・]|\d+[.、)])\s*", "", l).strip() for l in block.splitlines() if l.strip()]


def _call_claude(prompt: str) -> str:
    import anthropic

    cfg = CONFIG["claude"]
    client = anthropic.Anthropic()
    tools = []
    if cfg.get("use_web_search", True):
        tools.append({"type": "web_search_20260209", "name": "web_search", "max_uses": 6})

    messages = [{"role": "user", "content": prompt}]
    for _ in range(5):  # server tools may pause a long turn; resume until done
        resp = client.beta.messages.create(
            model=cfg.get("model", "claude-opus-5-5"),
            max_tokens=16000,
            system=SYSTEM,
            messages=messages,
            tools=tools,
            output_config={"effort": cfg.get("effort", "medium")},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        if resp.stop_reason == "refusal":
            raise RuntimeError("Claude 拒絕產生這次的內容")
        if resp.stop_reason != "pause_turn":
            break
        messages.append({"role": "assistant", "content": resp.content})
    return "".join(b.text for b in resp.content if b.type == "text")


def fallback(tw: dict, us: list[dict], date_label: str) -> dict:
    """沒有 Claude 時的固定模板。"""
    us_line = "、".join(f"{u['name']} {_fmt(u['change_pct'], '%')}" for u in us[:5])
    tw_text = summarize_tw(tw)
    body = f"📋 {date_label} 盤前籌碼整理\n\n🌎 昨夜國際盤：{us_line}\n\n📊 籌碼重點\n{tw_text}\n\n你今天怎麼看？留言聊聊 👇"
    return {
        "macro_points": [],
        "events": [],
        "threads": body,
        "ig": body,
    }


def write(tw: dict, glob: dict, diff: dict, date_label: str) -> dict:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("⚠️ 沒有設定 ANTHROPIC_API_KEY，改用固定模板")
        return fallback(tw, glob["us"], date_label)

    news = "\n".join(f"- [{n['published']}] {n['title']}｜{n['summary']}" for n in glob["news"][:40]) or "（無）"
    use_search = CONFIG["claude"].get("use_web_search", True)
    prompt = TASK.format(
        date_label=date_label,
        data_date=f"{tw['date'][:4]}/{tw['date'][4:6]}/{tw['date'][6:]}",
        tw=summarize_tw(tw),
        diff=json.dumps(diff, ensure_ascii=False),
        us=summarize_us(glob["us"]),
        news=news,
        search_hint=(
            "請用 web_search 查詢今天（台灣時間）與本週的重要經濟數據行事曆（例如美國 CPI、非農、FOMC、台灣央行、重要財報），"
            "以及昨夜有沒有上面新聞沒涵蓋的重大總經事件。" if use_search else ""
        ),
        voice=CONFIG["claude"].get("voice", ""),
    )
    try:
        text = _call_claude(prompt)
    except Exception as e:
        print(f"⚠️ Claude 產生失敗，改用固定模板：{e}")
        return fallback(tw, glob["us"], date_label)

    sec = _parse_sections(text)
    if not sec.get("THREADS"):
        print("⚠️ Claude 回傳格式不符，改用固定模板")
        return fallback(tw, glob["us"], date_label)
    return {
        "macro_points": _lines(sec.get("MACRO_POINTS", ""))[:5],
        "events": _lines(sec.get("EVENTS", ""))[:5],
        "threads": sec["THREADS"],
        "ig": sec.get("IG") or sec["THREADS"],
    }
