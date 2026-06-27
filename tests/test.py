import asyncio
import json
import sys
from pathlib import Path

from loguru import logger

project_root = Path('.').resolve()
sys.path.insert(0, str(project_root))

from app.db import init_db
from app.http_client import close_http_client
from app.services import key_service
from app.services.key_service import db_get_existing_keys, detect_provider_and_key
from app.utils.status_summary import count_status_codes, format_status_code_counts

JSONL = Path('/Users/nkpig/Downloads/rescan_db_hosts_20260626_110139.jsonl')
CONCURRENT = 40


def dedupe_against_db(items: list[dict]) -> tuple[list[dict], int]:
    """文件内去重后，再过滤库内已存在的 key。"""
    seen: set[str] = set()
    unique: list[dict] = []
    for item in items:
        raw = item.get('key', '').strip()
        if not raw or raw in seen:
            continue
        seen.add(raw)
        unique.append(item)

    candidate_keys = [
        ck for _, ck in (detect_provider_and_key(item['key']) for item in unique) if ck
    ]
    existing = db_get_existing_keys(candidate_keys)

    to_verify: list[dict] = []
    skipped = 0
    for item in unique:
        _, clean_key = detect_provider_and_key(item['key'])
        if clean_key and clean_key in existing:
            skipped += 1
            continue
        to_verify.append(item)
    return to_verify, skipped


async def main():
    init_db()
    items = []
    with JSONL.open(encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            items.append({'key': obj['key'], 'origin': obj.get('url', '')})
    logger.info('从 {} 读取 {} 条记录', JSONL.name, len(items))

    to_verify, skipped_existing = await asyncio.to_thread(dedupe_against_db, items)
    logger.info(
        '去重完成：文件内 {} 条 | 库内已存在跳过 {} 条 | 待校验 {} 条',
        len(items), skipped_existing, len(to_verify),
    )
    if not to_verify:
        logger.warning('无新密钥需要校验，退出')
        return

    logger.info('开始批量校验入库（并发 {}）...', CONCURRENT)
    results = await key_service.batch_process_keys(to_verify, concurrent=CONCURRENT)

    saved = sum(1 for r in results if r.get('saved'))
    skipped = sum(1 for r in results if r.get('status_detail') == 'skipped_existing')
    logger.success(
        '\n{}\n入库统计: 总计 {} | 新入库 {} | 已存在跳过 {} | 其他未入库 {}\n{}',
        '=' * 55, len(results), saved, skipped + skipped_existing, len(results) - saved - skipped, '=' * 55,
    )
    logger.info('校验状态码统计: {}', format_status_code_counts(count_status_codes(results)))


async def run():
    try:
        await main()
    finally:
        await close_http_client()
        logger.info('资源清理完成')


asyncio.run(run())
