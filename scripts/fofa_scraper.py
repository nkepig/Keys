#!/usr/bin/env python3
"""
FOFA 搜索 → URL 扫描 → 密钥提取 → 批量验证入库

扫站前会按历史表中的站点 netloc 进行去重，7 天内扫描过则跳过。

用法:
    python scripts/fofa_scraper.py
"""
import asyncio
import json
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

from loguru import logger

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from app.db import init_db
from app.http_client import close_http_client
from app.services import key_service
from app.services.fofa_service import fofa_search
from app.services.scanner_service import scan_urls
from app.utils.scan_history import SCAN_HISTORY_MATCH_NETLOC, SCAN_HISTORY_WINDOW_DAYS, load_recent_scan_history_targets, normalize_netloc, prune_scan_history, save_scan_history
from app.utils.status_summary import count_status_codes, format_status_code_counts


async def main():
    init_db()
    fofa_size = 10000
    scan_concurrent = 40
    verify_concurrent = 40

    try:
        date = (datetime.now() - timedelta(days=random.randint(1, 730))).strftime("%Y-%m-%d")
        q_oai = f'(body="gemini") || (body="api" || body="key") after="{date}"'
        q_ggl = f'(body="AIzaSy{random.choice("ABCD")}") after="{date}"'
        logger.info(f"FOFA 查询[OpenAI]: {q_oai}")
        logger.info(f"FOFA 查询[Google]: {q_ggl}")

        hosts_openai, hosts_google = await asyncio.gather(
            fofa_search(q_oai, size=fofa_size),
            fofa_search(q_ggl, size=fofa_size),
        )
        hosts = sorted(set(hosts_openai) | set(hosts_google))
        logger.info(f"共找到 {len(hosts)} 个目标 (OpenAI:{len(hosts_openai)}, Google:{len(hosts_google)})")

        if not hosts:
            logger.warning("FOFA 未返回任何目标，退出")
            return

        pruned = prune_scan_history(window_days=SCAN_HISTORY_WINDOW_DAYS)
        known = load_recent_scan_history_targets(
            source="fofa",
            match_type=SCAN_HISTORY_MATCH_NETLOC,
            window_days=SCAN_HISTORY_WINDOW_DAYS,
        )

        n0 = len(hosts)
        hosts = [x for x in hosts if (n := normalize_netloc(x)) is None or n not in known]
        logger.info(
            "按 {} 天历史表（netloc）过滤: 跳过 {} 个重复站点，剩余 {} 个待扫描（历史站点 {}，清理过期 {}）",
            SCAN_HISTORY_WINDOW_DAYS,
            n0 - len(hosts),
            len(hosts),
            len(known),
            pruned,
        )
        if not hosts:
            logger.warning("过滤后命中历史表，无新站点可扫，退出")
            return

        all_keys = await scan_urls(hosts, concurrent=scan_concurrent)
        saved_history = save_scan_history(hosts, source="fofa", match_type=SCAN_HISTORY_MATCH_NETLOC)
        logger.info("扫描历史已写入 {} 个站点（保留 {} 天）", saved_history, SCAN_HISTORY_WINDOW_DAYS)

        if all_keys:
            out_dir = project_root / "tmp"
            out_dir.mkdir(parents=True, exist_ok=True)
            path = out_dir / f"fofa_scraper_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
            try:
                with path.open("w", encoding="utf-8") as f:
                    for item in all_keys:
                        f.write(
                            json.dumps(
                                {"provider": item["provider"], "key": item["key"], "url": item.get("url", "")},
                                ensure_ascii=False,
                            )
                            + "\n"
                        )
                logger.info(f"已备份 {len(all_keys)} 条密钥 → {path}")
            except OSError as e:
                logger.error(f"备份写入失败: {e}")

        if not all_keys:
            logger.warning("页面扫描未匹配到任何密钥")
            return

        logger.info(f"开始批量校验 {len(all_keys)} 个密钥...")
        results = await key_service.batch_process_keys(
            [{"key": item["key"], "origin": item["url"]} for item in all_keys],
            concurrent=verify_concurrent,
        )

        saved = sum(1 for r in results if r["saved"])
        logger.success(
            f"\n{'='*55}\n"
            f"密钥入库统计: 总计 {len(results)} | 成功 {saved} | 失败 {len(results) - saved}\n"
            f"{'='*55}"
        )
        logger.info("校验状态码统计: {}", format_status_code_counts(count_status_codes(results)))

    except RuntimeError as e:
        logger.error(f"FOFA 请求失败: {e}")
    except Exception as e:
        logger.exception(f"执行出错: {e}")
    finally:
        await close_http_client()
        logger.info("资源清理完成")


if __name__ == "__main__":
    asyncio.run(main())
