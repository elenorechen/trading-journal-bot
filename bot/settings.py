"""Project paths and config loading."""
from __future__ import annotations

import os
import tomllib
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
POSTS_DIR = ROOT / "posts"
DATA_DIR = ROOT / "data"
TW_TZ = timezone(timedelta(hours=8))


def load_config() -> dict:
    with open(ROOT / "config.toml", "rb") as f:
        return tomllib.load(f)


CONFIG = load_config()


def now_tw() -> datetime:
    return datetime.now(TW_TZ)


def env(name: str, required: bool = True) -> str:
    value = os.environ.get(name, "").strip()
    if required and not value:
        raise SystemExit(f"缺少環境變數 {name}（請在 GitHub repo 的 Settings → Secrets 設定）")
    return value
