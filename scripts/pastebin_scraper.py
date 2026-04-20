#!/usr/bin/env python3
"""
Pastebin 搜索 → 浏览器扫页 → 密钥提取 → 批量校验入库。

在项目根目录执行（需已激活 venv）:

    python scripts/pastebin_scraper.py

账号、密码、关键词等均在下方常量中修改；无命令行参数。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

# ── 在此处填写配置 ───────────────────────────────────────────────────────────
PASTEBIN_PASSWORD = "abcd12345678"

PASTEBIN_ACCOUNTS = [
    "1775617332",
    "1775617354",
    "1775617381",
    "1775617416",
    "1775617437",
]

QUERIES = ["sk-", "openai", "api_key", "AIzaSy", "gemini"]

# None 表示使用默认：data/pastebin_scraper_urls.sqlite、data/pastebin_scraper_keys.json
SQLITE_PATH: str | None = None
KEYS_JSON_PATH: str | None = None

VERIFY = True
SCAN_CONCURRENT = 2
VERIFY_CONCURRENT = 40


async def main() -> None:
    from loguru import logger

    from app.pastebin.workflow import run_pastebin_scrape

    try:
        scan_results = await run_pastebin_scrape(
            pastebin_password=PASTEBIN_PASSWORD,
            pastebin_accounts=PASTEBIN_ACCOUNTS,
            queries=QUERIES,
            sqlite_path=SQLITE_PATH,
            keys_json_path=KEYS_JSON_PATH,
            scan_concurrent=SCAN_CONCURRENT,
            verify_concurrent=VERIFY_CONCURRENT,
            verify=VERIFY,
        )
        if scan_results:
            logger.info("运行结束，共 {} 条密钥记录（见 JSON）", len(scan_results))
        else:
            logger.warning("未发现任何密钥或未收集到链接")
    except Exception as e:
        logger.exception("程序异常: {}", e)
        raise


if __name__ == "__main__":
    asyncio.run(main())
