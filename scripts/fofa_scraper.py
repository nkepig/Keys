#!/usr/bin/env python3
"""
FOFA 搜索 → URL 扫描 → 密钥提取 → 批量验证入库

扫站前会按库中 Key.origin 的 URL 与 FOFA 目标比对 netloc（与 scanner 写入的 https://host 结构一致），已存在则跳过。

用法:
    python scripts/fofa_scraper.py
"""
import asyncio
import json
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

from loguru import logger
from sqlmodel import Session, select

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from app.db import engine
from app.http_client import close_http_client
from app.models.key import Key
from app.services import key_service
from app.services.fofa_service import fofa_search
from app.services.scanner_service import scan_urls
from app.utils.status_summary import count_status_codes, format_status_code_counts


def _netloc(s: str | None) -> str | None:
    """与库里 origin 同一套 URL 结构：取 netloc（含端口），小写。无 scheme 时补 http:// 再解析。"""
    if not s or not str(s).strip():
        return None
    t = str(s).strip()
    if "://" not in t:
        t = "http://" + t.split("/")[0]
    try:
        nl = urlparse(t).netloc.lower()
        return nl or None
    except Exception:
        return None


async def main():
    fofa_size = 5000
    scan_concurrent = 40
    verify_concurrent = 40

    try:
        date = (datetime.now() - timedelta(days=random.randint(1, 365))).strftime("%Y-%m-%d")
        q_oai = f'(body="sk-proj-" || body="sk-ant-api") && after="{date}"'
        q_ggl = f'(body="AIzaSy" || body="gemini" && body="key") && after="{date}"'
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

        with Session(engine) as session:
            known = {_netloc(o) for o in session.exec(select(Key.origin)).all()}
        known.discard(None)

        n0 = len(hosts)
        hosts = [x for x in hosts if (n := _netloc(x)) is None or n not in known]
        logger.info(f"按库内来源 URL（netloc）过滤: 跳过 {n0 - len(hosts)} 个重复站点，剩余 {len(hosts)} 个待扫描（库内不同来源 {len(known)}）")
        if not hosts:
            logger.warning("过滤后与库重复，无新站点可扫，退出")
            return

        all_keys = await scan_urls(hosts, concurrent=scan_concurrent)

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
