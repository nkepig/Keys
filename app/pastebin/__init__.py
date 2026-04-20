"""Pastebin 搜索爬虫（依赖 DrissionPage / ddddocr）。"""

from app.pastebin.workflow import (
    default_keys_json_path,
    default_sqlite_path,
    run_pastebin_scrape,
)

__all__ = [
    "default_keys_json_path",
    "default_sqlite_path",
    "run_pastebin_scrape",
]
