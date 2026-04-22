#!/usr/bin/env python3
"""
Kaggle 搜索 → URL 收集 → 页面扫描 → 密钥提取 → 校验入库

用法:
    python scripts/kaggle_scraper.py
"""
import asyncio
import json
import sys
from pathlib import Path

from loguru import logger

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from app.db import init_db
from app.http_client import close_http_client, get_http_session
from app.services import key_service
from app.services.scanner_service import scan_urls
from app.utils.status_summary import count_status_codes, format_status_code_counts
from app.utils.concurrency import gather_limited

BASE_URL = "https://www.kaggle.com"
QUERIES = [
    "openai sortBy:date",
    "sk- sortBy:date",
    "api_key sortBy:date",
    "sk-proj sortBy:date",
    "AIzaSy sortBy:date",
]


async def _get_authority() -> tuple[str, dict]:
    """获取 XSRF token 和 cookies"""
    session = get_http_session()
    async with session.get(BASE_URL) as response:
        cookies = {cookie.key: cookie.value for cookie in session.cookie_jar}
        xsrf_token = cookies.get("XSRF-TOKEN", "")
        return xsrf_token, cookies


async def search_kaggle(
    query: str,
    page: int = 1,
    results_per_page: int = 20,
    xsrf_token: str = "",
    cookies: dict | None = None,
) -> list[str]:
    """搜索 Kaggle 并提取所有 topicUrl"""
    session = get_http_session()
    cookie_header = "; ".join([f"{k}={v}" for k, v in (cookies or {}).items()])

    try:
        async with session.post(
            f"{BASE_URL}/api/i/search.SearchWebService/FullSearchWeb",
            headers={
                "Cookie": cookie_header,
                "content-type": "application/json",
                "x-xsrf-token": xsrf_token,
            },
            json={
                "query": query,
                "page": page,
                "resultsPerPage": results_per_page,
                "showPrivate": True,
            },
        ) as response:
            if response.status == 400:
                try:
                    err = await response.json()
                    logger.debug(f"API 错误: {json.dumps(err, ensure_ascii=False)}")
                except Exception:
                    logger.debug(f"API 错误（非JSON）: {(await response.text())[:200]}")

            response.raise_for_status()
            data = await response.json()
    except Exception as e:
        logger.error(f"Kaggle 搜索异常: {e}")
        return []

    urls = []
    for doc in data.get("documents", []):
        url = doc.get("url", "")
        if url:
            full_url = f"{BASE_URL}{url}" if url.startswith("/") else f"{BASE_URL}/{url}"
            urls.append(full_url)
    return urls


async def collect_all_urls(xsrf_token: str, cookies: dict) -> list[str]:
    """收集所有 query 第一页的 URL，在内存中去重"""
    seen: set[str] = set()
    all_urls: list[str] = []

    logger.info(f"📋 开始收集 URL，共 {len(QUERIES)} 个查询")

    for query in QUERIES:
        logger.info(f"🔍 正在搜索: {query}")
        try:
            urls = await search_kaggle(query=query, page=1, xsrf_token=xsrf_token, cookies=cookies)
            new_urls = [u for u in urls if u not in seen]
            seen.update(new_urls)
            all_urls.extend(new_urls)
            logger.info(f"  ✅ 找到 {len(new_urls)} 个新 URL（本页共 {len(urls)} 个）")
        except Exception as e:
            logger.error(f"  ❌ 搜索失败: {e}")

    logger.success(f"✅ URL 收集完成，共 {len(all_urls)} 个唯一 URL")
    return all_urls


async def scan_and_save_keys(urls: list[str]) -> list[dict]:
    """扫描 URL，提取并入库密钥"""
    if not urls:
        logger.warning("没有 URL 需要扫描")
        return []

    scan_results = await scan_urls(urls, concurrent=1)

    if not scan_results:
        logger.warning("页面扫描未找到任何密钥")
        return []

    results = await key_service.batch_process_keys(
        [{"key": item["key"], "origin": item["url"]} for item in scan_results],
        concurrent=10,
    )
    saved = sum(1 for r in results if r.get("saved"))
    logger.success(
        f"\n{'='*55}\n"
        f"📊 密钥入库统计: 总计 {len(results)} | 成功 {saved} | 其他 {len(results) - saved}\n"
        f"{'='*55}"
    )
    logger.info("校验状态码统计: {}", format_status_code_counts(count_status_codes(results)))
    return results


async def main():
    init_db()

    try:
        logger.info("🔑 获取 Kaggle 认证信息...")
        xsrf_token, cookies = await _get_authority()
        logger.info("✅ 认证信息获取成功")

        all_urls = await collect_all_urls(xsrf_token, cookies)

        if all_urls:
            await scan_and_save_keys(all_urls)
        else:
            logger.warning("未收集到任何 URL")

    except Exception as e:
        logger.exception(f"程序执行出错: {e}")
        raise
    finally:
        await close_http_client()
        logger.info("资源清理完成")


if __name__ == "__main__":
    asyncio.run(main())
