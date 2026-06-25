#!/usr/bin/env python3
"""
读取 key 管理器中已入库的全部 host（Key.origin），按 netloc 去重后逐个扫站，
提取页面中匹配到的 API 密钥并走批量校验入库。

逻辑：
  1. 从 keys 表读取所有非空且形似 URL 的 origin（host:port / ip / 域名）。
  2. 按 netloc 去重，得到待扫描的 host 列表。
  3. 用 scanner_service.scan_urls 并发抓取页面 → detect_all_keys_from_text 提取密钥。
  4. 用 key_service.batch_process_keys 去重 → LLM 校验 → 入库。
  5. 写入 scan_history（source="rescan_db"，netloc 维度），方便后续去重统计。

用法：
    python scripts/rescan_db_hosts.py
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

from loguru import logger
from sqlalchemy import text

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from app.db import engine, init_db
from app.http_client import close_http_client
from app.services import key_service
from app.services.scanner_service import scan_urls
from app.utils.scan_history import (
    SCAN_HISTORY_MATCH_NETLOC,
    normalize_netloc,
    prune_scan_history,
    save_scan_history,
)
from app.utils.status_summary import count_status_codes, format_status_code_counts

SOURCE = "rescan_db"
SCAN_CONCURRENT = 40
VERIFY_CONCURRENT = 40


def load_unique_hosts_from_db() -> list[str]:
    """读取 keys 表中所有非空且能归一化成 netloc 的 origin，按 netloc 去重。

    返回的元素是可直接喂给 scanner_service._fetch 的 URL；netloc 相同的只保留第一条出现的原始值。
    """
    seen_netloc: set[str] = set()
    hosts: list[str] = []
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT DISTINCT origin FROM keys WHERE origin IS NOT NULL AND origin != ''")
        ).fetchall()
    for (raw,) in rows:
        origin = (raw or "").strip()
        if not origin:
            continue
        # 只接受 http(s):// 形式的 origin；纯标签（如 'huggingface'）不可重放抓取
        if not origin.startswith(("http://", "https://")):
            continue
        netloc = normalize_netloc(origin)
        if not netloc or netloc in seen_netloc:
            continue
        seen_netloc.add(netloc)
        hosts.append(origin)
    return hosts


async def main() -> None:
    init_db()
    hosts = load_unique_hosts_from_db()
    logger.info("从 keys 表读到 {} 个唯一 netloc 的 host", len(hosts))

    if not hosts:
        logger.warning("没有待扫描的 host，退出")
        return

    pruned = prune_scan_history()  # 顺带清理过期历史
    logger.info("清理过期 scan_history（保留 7 天）: {} 行", pruned)

    all_keys = await scan_urls(hosts, concurrent=SCAN_CONCURRENT)
    saved_history = save_scan_history(
        hosts, source=SOURCE, match_type=SCAN_HISTORY_MATCH_NETLOC
    )
    logger.info("scan_history 已写入 {} 个 netloc", saved_history)

    if all_keys:
        out_dir = project_root / "tmp"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"rescan_db_hosts_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
        try:
            with path.open("w", encoding="utf-8") as f:
                for item in all_keys:
                    f.write(
                        json.dumps(
                            {
                                "provider": item["provider"],
                                "key": item["key"],
                                "url": item.get("url", ""),
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
            logger.info("已备份 {} 条原始密钥 → {}", len(all_keys), path)
        except OSError as e:
            logger.error("备份写入失败: {}", e)
    else:
        logger.warning("页面扫描未匹配到任何密钥")
        return

    logger.info("开始批量校验 {} 个密钥...", len(all_keys))
    results = await key_service.batch_process_keys(
        [{"key": item["key"], "origin": item["url"]} for item in all_keys],
        concurrent=VERIFY_CONCURRENT,
    )

    saved = sum(1 for r in results if r.get("saved"))
    logger.success(
        "\n{}\n密钥入库统计: 总计 {} | 成功 {} | 失败 {}\n{}",
        "=" * 55, len(results), saved, len(results) - saved, "=" * 55
    )
    logger.info("校验状态码统计: {}", format_status_code_counts(count_status_codes(results)))


async def run() -> None:
    try:
        await main()
    finally:
        await close_http_client()
        logger.info("资源清理完成")


if __name__ == "__main__":
    asyncio.run(run())