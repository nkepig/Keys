#!/usr/bin/env python3
"""
FOFA 搜索 → URL 扫描 → 密钥提取 → 批量验证入库

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

from app.http_client import close_http_client
from app.services import key_service
from app.services.fofa_service import fofa_search
from app.services.scanner_service import scan_urls

def _build_query(provider: str) -> str:
    date = (datetime.now() - timedelta(days=random.randint(1, 365))).strftime("%Y-%m-%d")
    queries = {
        "OpenAI":    f'(body="sk-proj-" || body="sk-ant-api") && after="{date}"',
        "Google":    f'(body="AIzaSy" || body="gemini" && body="key") && after="{date}"',
    }
    q = queries[provider]
    logger.info(f"FOFA 查询[{provider}]: {q}")
    return q


def _write_backup(keys: list[dict]) -> Path | None:
    if not keys:
        return None
    out_dir = project_root / "tmp"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"fofa_scraper_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
    try:
        with path.open("w", encoding="utf-8") as f:
            for item in keys:
                f.write(json.dumps(
                    {"provider": item["provider"], "key": item["key"], "url": item.get("url", "")},
                    ensure_ascii=False,
                ) + "\n")
        logger.info(f"已备份 {len(keys)} 条密钥 → {path}")
        return path
    except OSError as e:
        logger.error(f"备份写入失败: {e}")
        return None


async def main():
    fofa_size = 5000
    scan_concurrent = 40
    verify_concurrent = 40

    try:
        hosts_openai, hosts_google = await asyncio.gather(
            fofa_search(_build_query("OpenAI"), size=fofa_size),
            fofa_search(_build_query("Google"), size=fofa_size),
        )
        hosts = sorted(set(hosts_openai) | set(hosts_google))
        logger.info(f"共找到 {len(hosts)} 个目标 (OpenAI:{len(hosts_openai)}, Google:{len(hosts_google)})")

        if not hosts:
            logger.warning("FOFA 未返回任何目标，退出")
            return

        all_keys = await scan_urls(hosts, concurrent=scan_concurrent)
        _write_backup(all_keys)

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

    except RuntimeError as e:
        logger.error(f"FOFA 请求失败: {e}")
    except Exception as e:
        logger.exception(f"执行出错: {e}")
    finally:
        await close_http_client()
        logger.info("资源清理完成")


if __name__ == "__main__":
    asyncio.run(main())
