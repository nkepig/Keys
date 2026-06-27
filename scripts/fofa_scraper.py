#!/usr/bin/env python3
"""
FOFA 搜索 → URL 扫描 → 密钥提取 → 批量验证入库

扫站前会按历史表中的站点 netloc 进行去重，7 天内扫描过则跳过。
去重后不足 1000 个 URL 时会继续 FOFA 检索；达到 1000 后停止检索，当轮全部 URL 均参与扫描（不截断）。

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
    min_hosts = 3000
    scan_concurrent = 40
    verify_concurrent = 40

    try:
        query_templates: list[tuple[str, str]] = [
            ("gemini", '((body="gemini")) && after="{date}"'),
            ("gemini2", '(body="AIzaSy{prefix}") && after="{date}"'),
            ("gemini3", '(body="GoogleGenAI") && after="{date}"'),
            ("gemini4", '(body="googleapis") && after="{date}"'),
            ("gemini5", '(body="generateContent") && after="{date}"'),
            ("gemini6", '(body="x-goog-api-key") && after="{date}"'),
            ("gemini7", '(body="GEMINI_API_KEY") && after="{date}"'),
            ("openai", '((body="openai")) && after="{date}"'),
            ("openai2", '((body="sk-")) && after="{date}"'),
            # ("claude", '((body="anthropic")) && after="{date}"'),
            # ("claude2", '((body="sk-ant-")) && after="{date}"'),
        ]

        pruned = prune_scan_history(window_days=SCAN_HISTORY_WINDOW_DAYS)
        known = load_recent_scan_history_targets(
            source="fofa",
            match_type=SCAN_HISTORY_MATCH_NETLOC,
            window_days=SCAN_HISTORY_WINDOW_DAYS,
        )
        logger.info(
            "历史表（netloc，{} 天）: {} 个已知站点，清理过期 {} 个",
            SCAN_HISTORY_WINDOW_DAYS,
            len(known),
            pruned,
        )

        hosts: list[str] = []
        seen_netlocs: set[str] = set(known)
        round_num = 0
        empty_rounds = 0
        max_empty_rounds = len(query_templates) * 3

        while len(hosts) < min_hosts:
            round_num += 1
            if empty_rounds >= max_empty_rounds:
                logger.warning(
                    "连续 {} 轮检索无新增站点，已累积 {} 个（目标 {}），停止检索",
                    empty_rounds,
                    len(hosts),
                    min_hosts,
                )
                break

            date = (datetime.now() - timedelta(days=random.randint(1, 730))).strftime("%Y-%m-%d")
            name, tmpl = random.choice(query_templates)
            query = tmpl.format(date=date, prefix=random.choice("ABCD"))
            raw_hosts = await fofa_search(query, size=fofa_size)

            n_before = len(hosts)
            for x in raw_hosts:
                n = normalize_netloc(x)
                if n is not None and n in seen_netlocs:
                    continue
                hosts.append(x)
                if n is not None:
                    seen_netlocs.add(n)

            added = len(hosts) - n_before
            logger.info(
                "第 {} 轮 FOFA ({})：返回 {}，去重后新增 {}，累计 {}（目标 {}）",
                round_num,
                name,
                len(raw_hosts),
                added,
                len(hosts),
                min_hosts,
            )

            if added == 0:
                empty_rounds += 1
            else:
                empty_rounds = 0

            if len(hosts) >= min_hosts:
                break

        if not hosts:
            logger.warning("过滤后命中历史表，无新站点可扫，退出")
            return

        if len(hosts) < min_hosts:
            logger.warning("未达到目标 {} 个 URL，将扫描已累积的 {} 个", min_hosts, len(hosts))

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
