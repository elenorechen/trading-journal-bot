"""產生 IG / Threads 用的 1080x1350 圖卡（JPEG，IG 只接受 JPEG）。

台股慣例：紅色 = 上漲 / 買超，綠色 = 下跌 / 賣超。
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .settings import CONFIG

W, H = 1080, 1350
PAD = 64

BG = (13, 17, 28)
PANEL = (24, 30, 46)
LINE = (44, 52, 74)
TEXT = (236, 239, 245)
MUTED = (140, 149, 170)
ACCENT = (250, 196, 64)
UP = (255, 82, 82)      # 紅 = 漲
DOWN = (34, 197, 94)    # 綠 = 跌
FLAT = (180, 186, 200)

WEEKDAYS = "一二三四五六日"
NO_LINE_START = "，。、！？：；）」』》%,.!?:;)"


# ---------------------------------------------------------------- fonts

_CANDIDATES = {
    False: [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
        "C:/Windows/Fonts/msjh.ttc",
        "/System/Library/Fonts/PingFang.ttc",
    ],
    True: [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc",
        "C:/Windows/Fonts/msjhbd.ttc",
        "/System/Library/Fonts/PingFang.ttc",
    ],
}


@lru_cache(maxsize=4)
def _font_file(bold: bool) -> tuple[str, int]:
    custom = CONFIG.get("fonts", {}).get("bold" if bold else "regular", "")
    paths = [custom] if custom else _CANDIDATES[bold]
    for p in paths:
        if not p or not os.path.exists(p):
            continue
        # Noto CJK 的 .ttc 內含日/韓/簡/繁，挑繁中（TC）那一個
        for idx in range(10):
            try:
                name = ImageFont.truetype(p, 20, index=idx).getname()[0]
            except OSError:
                break
            if "TC" in name:
                return p, idx
        return p, 0
    raise SystemExit("找不到中文字型。GitHub 上請安裝 fonts-noto-cjk，或在 config.toml 的 [fonts] 指定字型路徑。")


@lru_cache(maxsize=64)
def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    path, idx = _font_file(bold)
    return ImageFont.truetype(path, size, index=idx)


# ---------------------------------------------------------------- helpers

def color_of(v) -> tuple:
    if v is None or v == 0:
        return FLAT
    return UP if v > 0 else DOWN


def signed(v, digits=0, unit="") -> str:
    if v is None:
        return "—"
    return f"{v:+,.{digits}f}{unit}"


def arrow(v) -> str:
    if v is None or v == 0:
        return ""
    return "▲" if v > 0 else "▼"


def wrap(draw: ImageDraw.ImageDraw, text: str, f, max_w: int) -> list[str]:
    """CJK-friendly wrapping (break anywhere, keep latin words together when possible)."""
    lines: list[str] = []
    for para in text.split("\n"):
        cur = ""
        for ch in para:
            # 標點不放行首：寧可讓這一行稍微超出一點
            if draw.textlength(cur + ch, font=f) <= max_w or (cur and ch in NO_LINE_START):
                cur += ch
            else:
                lines.append(cur)
                cur = ch.lstrip()
        lines.append(cur)
    return lines


def date_label(d) -> str:
    return f"{d.year}/{d.month:02d}/{d.day:02d}（{WEEKDAYS[d.weekday()]}）"


class Card:
    def __init__(self, title: str, subtitle: str):
        self.img = Image.new("RGB", (W, H), BG)
        self.d = ImageDraw.Draw(self.img)
        brand = CONFIG["brand"]
        # header
        self.d.rectangle([0, 0, W, 10], fill=ACCENT)
        self.d.text((PAD, 44), brand.get("name", ""), font=font(28, True), fill=ACCENT)
        self.d.text((PAD, 86), title, font=font(60, True), fill=TEXT)
        self.d.text((PAD, 166), subtitle, font=font(28), fill=MUTED)
        self.y = 230
        # footer
        self.d.line([PAD, H - 118, W - PAD, H - 118], fill=LINE, width=2)
        self.d.text((PAD, H - 100), brand.get("handle", ""), font=font(26, True), fill=TEXT)
        disc = "個人交易紀錄，非投資建議"
        tw = self.d.textlength(disc, font=font(22))
        self.d.text((W - PAD - tw, H - 96), disc, font=font(22), fill=MUTED)
        self.d.text((PAD, H - 58), "資料來源：臺灣證券交易所、臺灣期貨交易所、Yahoo Finance、鉅亨網", font=font(18), fill=MUTED)

    def section(self, label: str):
        self.d.rectangle([PAD, self.y + 6, PAD + 8, self.y + 38], fill=ACCENT)
        self.d.text((PAD + 22, self.y), label, font=font(32, True), fill=TEXT)
        self.y += 58

    def panel(self, x0, y0, x1, y1):
        self.d.rounded_rectangle([x0, y0, x1, y1], radius=18, fill=PANEL)

    def save(self, path: Path):
        self.img.save(path, "JPEG", quality=92, optimize=True)


# ---------------------------------------------------------------- cards

def chips_card(tw: dict, diffs: dict, today, path: Path):
    dd = tw["date"]
    c = Card("盤前籌碼總覽", f"{date_label(today)}　｜　籌碼資料日 {dd[4:6]}/{dd[6:]}")
    d = c.d

    # 加權指數 hero
    if t := tw.get("taiex"):
        c.panel(PAD, c.y, W - PAD, c.y + 160)
        d.text((PAD + 32, c.y + 18), "加權指數", font=font(26), fill=MUTED)
        d.text((PAD + 32, c.y + 50), f"{t['close']:,.2f}", font=font(56, True), fill=TEXT)
        col = color_of(t["change"])
        d.text((PAD + 32, c.y + 118), f"{arrow(t['change'])} {abs(t['change']):,.2f} 點（{t['change_pct']:+.2f}%）", font=font(28, True), fill=col)
        d.text((W - PAD - 260, c.y + 18), "成交金額", font=font(26), fill=MUTED)
        d.text((W - PAD - 260, c.y + 58), f"{t['turnover']:,.0f} 億", font=font(44, True), fill=TEXT)
        c.y += 178

    # 現貨三大法人 diverging bars
    c.section("現貨　三大法人買賣超（億元）")
    if s := tw.get("stock_inst"):
        items = [("外資", s["foreign"]), ("投信", s["trust"]), ("自營商", s["dealer"]), ("合計", s["total"])]
        scale = max(abs(v) for _, v in items) or 1
        mid = PAD + 650
        half = 280
        for name, v in items:
            d.text((PAD + 10, c.y), name, font=font(30, name == "合計"), fill=TEXT)
            val = signed(v, 2)
            vx = PAD + 340 - d.textlength(val, font=font(30, True))
            d.text((vx, c.y), val, font=font(30, True), fill=color_of(v))
            d.line([mid, c.y - 4, mid, c.y + 40], fill=LINE, width=2)
            bw = int(abs(v) / scale * half)
            if v >= 0:
                d.rounded_rectangle([mid, c.y + 4, mid + max(bw, 4), c.y + 34], radius=6, fill=UP)
            else:
                d.rounded_rectangle([mid - max(bw, 4), c.y + 4, mid, c.y + 34], radius=6, fill=DOWN)
            c.y += 52
        c.y += 14

    # 期貨 tiles
    c.section("期貨　台指期籌碼")
    fut = (tw.get("fut_inst") or {}).get("tx", {})
    lg = tw.get("large") or {}
    pcr = tw.get("pcr") or {}
    tiles = [
        ("外資淨未平倉", signed(fut.get("foreign", {}).get("oi_net"), unit=" 口"), fut.get("foreign", {}).get("oi_net"), diffs.get("foreign_tx_oi"), " 口"),
        ("投信淨未平倉", signed(fut.get("trust", {}).get("oi_net"), unit=" 口"), fut.get("trust", {}).get("oi_net"), diffs.get("trust_tx_oi"), " 口"),
        ("自營商淨未平倉", signed(fut.get("dealer", {}).get("oi_net"), unit=" 口"), fut.get("dealer", {}).get("oi_net"), diffs.get("dealer_tx_oi"), " 口"),
        ("前十大交易人", signed(lg.get("top10_net"), unit=" 口"), lg.get("top10_net"), diffs.get("top10_net"), " 口"),
        ("小台散戶多空比", signed(tw.get("retail_ratio"), 2, "%"), tw.get("retail_ratio"), diffs.get("retail_ratio"), "%"),
        ("P/C 未平倉比", f"{pcr['oi_ratio']:.2f}%" if pcr else "—", None, diffs.get("pcr_oi"), "%"),
    ]
    tw_w = (W - 2 * PAD - 2 * 20) // 3
    th = 128
    for i, (label, value, sign_v, delta, unit) in enumerate(tiles):
        x0 = PAD + (i % 3) * (tw_w + 20)
        y0 = c.y + (i // 3) * (th + 18)
        c.panel(x0, y0, x0 + tw_w, y0 + th)
        d.text((x0 + 22, y0 + 16), label, font=font(24), fill=MUTED)
        d.text((x0 + 22, y0 + 46), value, font=font(36, True), fill=color_of(sign_v) if sign_v is not None else TEXT)
        dtxt = "較前日 " + (signed(delta, 2 if unit == "%" else 0, unit) if delta is not None else "—")
        d.text((x0 + 22, y0 + 92), dtxt, font=font(22), fill=color_of(delta) if delta is not None else MUTED)
    c.y += 2 * th + 18 + 28

    # 融資融券
    if mg := tw.get("margin"):
        c.section("信用交易")
        half_w = (W - 2 * PAD - 20) // 2
        for i, (label, val, delta) in enumerate([
            ("融資餘額", f"{mg['margin_balance']:,.0f} 億", signed(mg["margin_change"], 2, " 億")),
            ("融券餘額", f"{mg['short_balance']:,} 張", signed(mg["short_change"], unit=" 張")),
        ]):
            x0 = PAD + i * (half_w + 20)
            raw = mg["margin_change"] if i == 0 else mg["short_change"]
            c.panel(x0, c.y, x0 + half_w, c.y + 90)
            d.text((x0 + 22, c.y + 12), label, font=font(24), fill=MUTED)
            d.text((x0 + 22, c.y + 42), val, font=font(32, True), fill=TEXT)
            dx = x0 + half_w - 22 - d.textlength(delta, font=font(28, True))
            d.text((dx, c.y + 46), delta, font=font(28, True), fill=color_of(raw))
    c.save(path)


def global_card(us: list[dict], today, path: Path):
    c = Card("昨夜國際盤", f"{date_label(today)}　｜　美股收盤與跨市場指標")
    d = c.d
    row_h = 84
    c.panel(PAD, c.y, W - PAD, c.y + row_h * len(us) + 24)
    y = c.y + 18
    scale = max((abs(u["change_pct"]) for u in us), default=1) or 1
    for i, u in enumerate(us):
        if i:
            d.line([PAD + 24, y - 6, W - PAD - 24, y - 6], fill=LINE, width=1)
        d.text((PAD + 30, y + 12), u["name"], font=font(30), fill=TEXT)
        d.text((PAD + 360, y + 10), f"{u['close']:,}", font=font(32, True), fill=TEXT)
        col = color_of(u["change_pct"])
        chg = f"{u['change']:+,.1f} bp" if u["unit"] == "bp" else f"{u['change_pct']:+.2f}%"
        d.text((PAD + 610, y + 10), chg, font=font(32, True), fill=col)
        bw = int(abs(u["change_pct"]) / scale * 140)
        d.rounded_rectangle([W - PAD - 30 - max(bw, 4), y + 22, W - PAD - 30, y + 42], radius=5, fill=col)
        y += row_h
    c.save(path)


def macro_card(points: list[str], events: list[str], trend: list[tuple[str, float]], today, path: Path):
    c = Card("今日總經重點", f"{date_label(today)}　｜　盤前必看")
    d = c.d
    if points:
        c.section("重點整理")
        for p in points:
            lines = wrap(d, p, font(32), W - 2 * PAD - 70)
            d.ellipse([PAD + 14, c.y + 16, PAD + 28, c.y + 30], fill=ACCENT)
            for ln in lines:
                d.text((PAD + 48, c.y), ln, font=font(32), fill=TEXT)
                c.y += 46
            c.y += 14
        c.y += 10
    if events:
        c.section("今日 / 本週行事曆")
        for e in events:
            for ln in wrap(d, e, font(28), W - 2 * PAD - 40):
                d.text((PAD + 30, c.y), ln, font=font(28), fill=TEXT)
                c.y += 40
            c.y += 8
        c.y += 10

    # 外資台指期淨未平倉趨勢
    if len(trend) >= 3 and c.y < H - 460:
        c.section(f"外資台指期淨未平倉　近 {len(trend)} 日（口）")
        x0, y0, x1, y1 = PAD, c.y, W - PAD, min(c.y + 300, H - 150)
        c.panel(x0, y0, x1, y1)
        vals = [v for _, v in trend]
        lo, hi = min(vals + [0]), max(vals + [0])
        span = (hi - lo) or 1
        ix0, ix1, iy0, iy1 = x0 + 40, x1 - 40, y0 + 40, y1 - 50

        def py(v):
            return iy1 - (v - lo) / span * (iy1 - iy0)

        d.line([ix0, py(0), ix1, py(0)], fill=LINE, width=2)
        step = (ix1 - ix0) / max(len(vals) - 1, 1)
        bw = max(int(step * 0.6), 4)
        for i, v in enumerate(vals):
            cx = ix0 + i * step
            top, bot = sorted([py(v), py(0)])
            d.rectangle([cx - bw / 2, top, cx + bw / 2, max(bot, top + 2)], fill=color_of(v))
        first, last = trend[0][0], trend[-1][0]
        d.text((ix0 - 10, y1 - 38), f"{first[4:6]}/{first[6:]}", font=font(20), fill=MUTED)
        lt = f"{last[4:6]}/{last[6:]}  {vals[-1]:+,.0f}"
        d.text((ix1 - d.textlength(lt, font=font(20)), y1 - 38), lt, font=font(20), fill=MUTED)
    c.save(path)


def plan_card(chart: Image.Image, title: str, today, path: Path, page: str = ""):
    """把 TradingView 截圖放進品牌框裡，維持所有圖卡同一個尺寸。"""
    c = Card("盤前規劃", f"{date_label(today)}　{page}")
    d = c.d
    y = c.y
    if title:
        for ln in wrap(d, title, font(36, True), W - 2 * PAD)[:2]:
            d.text((PAD, y), ln, font=font(36, True), fill=ACCENT)
            y += 52
        y += 10
    box_w, box_h = W - 2 * PAD, (H - 140) - y
    chart = chart.convert("RGB")
    ratio = min(box_w / chart.width, box_h / chart.height)
    resized = chart.resize((int(chart.width * ratio), int(chart.height * ratio)), Image.LANCZOS)
    ox = PAD + (box_w - resized.width) // 2
    oy = y + (box_h - resized.height) // 2
    c.img.paste(resized, (ox, oy))
    d.rectangle([ox - 1, oy - 1, ox + resized.width, oy + resized.height], outline=LINE, width=2)
    c.save(path)
