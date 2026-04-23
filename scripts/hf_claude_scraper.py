#!/usr/bin/env python3
"""
HuggingFace 全文搜索 → 提取 Anthropic 密钥 (sk-ant-api03-...) → 校验入库

用法:
    python scripts/hf_claude_scraper.py
"""
import asyncio
import random
import re
import string
import sys
from pathlib import Path

from loguru import logger

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from app.db import init_db
from app.http_client import close_http_client, get_http_session
from app.services import key_service
from app.utils.status_summary import count_status_codes, format_status_code_counts

BASE_URL = "https://huggingface.co"
API_URL = f"{BASE_URL}/api/search/full-text"

_HEADERS = {
    "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
}

# 与 app/services/key_service.py Anthropic 规则一致：sk-ant-(api|sid)\d{2}-...
_KEY_PATTERN = re.compile(r"ant-(api|sid)(\d{2})-([A-Za-z0-9_-]{10,})")


def generate_random_query() -> str:
    """生成随机查询关键词 k-ant-api03-[a-zA-Z0-9][a-zA-Z0-9]"""
    chars = string.ascii_letters + string.digits
    return f"k-ant-api03-{random.choice(chars)}"


async def search_huggingface(q: str, limit: int = 20, skip: int = 0) -> dict:
    session = get_http_session()
    url = f"{API_URL}?q={q}&limit={limit}&skip={skip}&type=model&type=dataset&type=space"
    try:
        async with session.get(url, headers=_HEADERS) as response:
            if response.status != 200:
                logger.error(f"HuggingFace API 请求失败，状态码: {response.status}")
                return {}
            return await response.json()
    except Exception as e:
        logger.error(f"请求异常: {e}")
        return {}


def _extract_file_content(data: dict) -> str:
    parts = []
    for hit in data.get("hits", []):
        for item in hit.get("formatted", {}).get("fileContent", []):
            if isinstance(item, dict) and "text" in item:
                parts.append(item["text"])
    return "".join(parts)


def _extract_claude_keys(text: str) -> list[str]:
    keys: list[str] = []
    for api_or_sid, digits, tail in _KEY_PATTERN.findall(text):
        full_key = f"sk-ant-{api_or_sid}{digits}-{tail}"
        if full_key not in keys:
            keys.append(full_key)
            logger.info(f"🔑 发现密钥: {full_key[:24]}...")
    return keys


async def search_and_extract_keys(q: str) -> list[str]:
    all_keys: list[str] = []
    skip = 0
    limit = 20

    while True:
        logger.info(f"🔍 搜索 HuggingFace: q={q}, skip={skip}")
        data = await search_huggingface(q=q, limit=limit, skip=skip)
        if not data:
            break

        hits = data.get("hits", [])
        logger.info(f"📊 预估总结果数: {data.get('estimatedTotalHits', 0)}, 当前页: {len(hits)}")
        if not hits:
            break

        for key in _extract_claude_keys(_extract_file_content(data)):
            if key not in all_keys:
                all_keys.append(key)

        if len(hits) < limit:
            break

        skip += limit
        await asyncio.sleep(0.5)

    return all_keys


async def main():
    init_db()
    round_count = 0

    try:
        while True:
            round_count += 1
            query = generate_random_query()
            logger.info(f"🔄 第 {round_count} 轮 | 关键词: {query}")

            keys = await search_and_extract_keys(q=query)
            unique_keys = list(dict.fromkeys(keys))
            logger.success(f"✅ 本轮找到 {len(unique_keys)} 个唯一密钥")

            if unique_keys:
                results = await key_service.batch_process_keys(
                    [{"key": k, "origin": "huggingface"} for k in unique_keys],
                    concurrent=10,
                )
                saved = sum(1 for r in results if r.get("saved"))
                logger.success(
                    f"\n{'='*55}\n"
                    f"📊 第 {round_count} 轮统计: "
                    f"总计 {len(results)} | 成功入库 {saved} | 其他 {len(results) - saved}\n"
                    f"{'='*55}"
                )
                logger.info("第 {} 轮校验状态码统计: {}", round_count, format_status_code_counts(count_status_codes(results)))

            logger.info(f"⏳ 等待 2 秒后开始第 {round_count + 1} 轮...")
            await asyncio.sleep(2)

    except KeyboardInterrupt:
        logger.info("🛑 收到中断信号，程序退出")
    except Exception as e:
        logger.exception(f"程序执行出错: {e}")
        raise
    finally:
        await close_http_client()
        logger.info("资源清理完成")


if __name__ == "__main__":
    asyncio.run(main())
