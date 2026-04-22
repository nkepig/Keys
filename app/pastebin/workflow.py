"""Pastebin 搜索 → 浏览器扫页 → 可选校验入库。"""
from __future__ import annotations

import asyncio
import json
import os
import random
from pathlib import Path
from typing import List, Optional

from loguru import logger

from app.db import init_db
from app.http_client import close_http_client
from app.pastebin.browser import AutoBrowseService
from app.pastebin.scanner import scan_pastebin_urls
from app.pastebin.url_store import (
    batch_save_urls_sqlite,
    filter_new_urls_local,
    ensure_parent_dir,
    init_urls_table_sqlite,
    load_existing_urls_sqlite,
)
from app.utils.scan_history import SCAN_HISTORY_MATCH_TARGET, SCAN_HISTORY_WINDOW_DAYS, load_recent_scan_history_targets, save_scan_history

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

BASE_URL = "https://pastebin.com"
SEARCH_URL = f"{BASE_URL}/search"

DEFAULT_QUERIES = ["sk-", "openai", "api_key", "AIzaSy", "gemini"]


def default_sqlite_path() -> str:
    return os.environ.get(
        "PASTEBIN_SCRAPER_SQLITE",
        str(_PROJECT_ROOT / "data" / "pastebin_scraper_urls.sqlite"),
    )


def default_keys_json_path() -> str:
    return os.environ.get(
        "PASTEBIN_SCRAPER_KEYS_JSON",
        str(_PROJECT_ROOT / "data" / "pastebin_scraper_keys.json"),
    )


def pick_account(accounts: list[str]) -> Optional[str]:
    cleaned = [u.strip() for u in accounts if u and str(u).strip()]
    return random.choice(cleaned) if cleaned else None


def generate_search_urls(queries: list[str]) -> List[str]:
    qlist = queries
    search_urls: list[str] = []
    for query in qlist:
        for page_num in range(1, 3):
            params = {"q": f'"{query}"', "page": page_num, "sort": "-date"}
            param_str = "&".join([f"{k}={v}" for k, v in params.items()])
            search_urls.append(f"{SEARCH_URL}?{param_str}")
    return search_urls


def normalize_pastebin_href(href: str) -> str:
    href = (href or "").strip()
    if not href:
        return ""
    if href.startswith("http://") or href.startswith("https://"):
        return href
    if href.startswith("/"):
        return f"{BASE_URL}{href}"
    return f"{BASE_URL}/{href.lstrip('/')}"


async def login_pastebin(
    browser: AutoBrowseService,
    *,
    password: str,
    accounts: list[str],
) -> None:
    pwd = password.strip()
    if not pwd:
        raise RuntimeError("未配置 Pastebin 密码：请在脚本中填写 pastebin_password")

    tab = browser.tab
    tab.get(BASE_URL)

    max_turnstile_retries = 3
    for retry in range(max_turnstile_retries):
        if browser.detect_turnstile():
            logger.info("首页 Turnstile（第 {}/{} 次）", retry + 1, max_turnstile_retries)
            turnstile_token = browser.bypass_turnstile(max_attempts=10, wait_interval=1.0)
            if turnstile_token:
                await asyncio.sleep(3)
                break
        else:
            logger.info("首页未检测到 Turnstile")
            break

    tab.ele("xpath:/html/body/div[1]/div[1]/div/div/div[2]/div/a[1]").click()
    await asyncio.sleep(5)

    for retry in range(max_turnstile_retries):
        if browser.detect_turnstile():
            logger.info("登录页 Turnstile（第 {}/{} 次）", retry + 1, max_turnstile_retries)
            turnstile_token = browser.bypass_turnstile(max_attempts=10, wait_interval=1.0)
            if turnstile_token:
                break
        else:
            logger.info("登录页未检测到 Turnstile")
            break

    await asyncio.sleep(3)
    account = pick_account(accounts)
    if not account:
        raise RuntimeError("未配置 Pastebin 账号：请在脚本中填写 pastebin_accounts 列表")

    tab.ele('xpath://*[@id="loginform-username"]').input(account)
    tab.ele('xpath://*[@id="loginform-password"]').input(pwd)

    for _ in range(5):
        img = tab.ele('xpath://*[@id="loginform-verifycode-image"]', timeout=1)
        if not img:
            tab.ele('xpath://*[@id="w0"]/div[4]/div[3]/button').click()
            break
        captcha_code = await browser.recognize_captcha(img.get_screenshot(as_bytes="png"))
        tab.ele('xpath://*[@id="loginform-verifycode"]').clear().input(captcha_code)
        tab.ele('xpath://*[@id="w0"]/div[4]/div[4]/button').click()
        await asyncio.sleep(1)
        if not tab.ele('xpath://*[@id="loginform-verifycode-image"]', timeout=1):
            break


async def process_search_url(browser: AutoBrowseService, search_url: str):
    tab = browser.tab
    tab.get(search_url)
    limit_element = tab.ele('xpath://*[@id="w1"]/div/div', timeout=2)
    if limit_element:
        limit_text = limit_element.text or ""
        if "limit" in limit_text.lower() or "24 hours" in limit_text.lower():
            logger.warning("检测到 24 小时搜索限制: {}", search_url)
            return "limit_reached"

    eles = tab.eles('xpath://*[@class="post-search-item"]/div/a')
    if not eles:
        logger.info("未找到链接元素: {}", search_url)
        return []

    links = []
    for ele in eles:
        href = ele.attr("href")
        norm = normalize_pastebin_href(href or "")
        if norm:
            links.append(norm)

    if not links:
        logger.info("0 个有效链接: {}", search_url)
        return []

    logger.info("{} 找到 {} 个链接", search_url, len(links))
    return links


async def run_pastebin_scrape(
    *,
    pastebin_password: str,
    pastebin_accounts: list[str],
    queries: list[str] | None = None,
    sqlite_path: str | None = None,
    keys_json_path: str | None = None,
    scan_concurrent: int = 2,
    verify_concurrent: int = 40,
    verify: bool = True,
) -> list[dict]:
    """
    登录 Pastebin → 收集搜索链接 → 浏览器扫描页面 → 写 JSON / URL 库 → 可选 batch_process_keys 入库。

    账号密码与搜索词由调用方（如 scripts/pastebin_scraper.py）传入。
    """
    sqlite_path = sqlite_path or default_sqlite_path()
    keys_json_path = keys_json_path or default_keys_json_path()
    init_db()

    qlist = queries if queries else DEFAULT_QUERIES

    await init_urls_table_sqlite(sqlite_path)
    existing_urls = await load_existing_urls_sqlite(sqlite_path)
    logger.info("已从 SQLite 加载 {} 个已存在 URL", len(existing_urls))

    all_links: List[str] = []
    remaining_urls = generate_search_urls(qlist)
    total_urls = len(remaining_urls)
    browser: Optional[AutoBrowseService] = None

    logger.info("共 {} 个搜索 URL", total_urls)

    try:
        while remaining_urls:
            try:
                if browser:
                    try:
                        browser.close()
                    except Exception:
                        pass

                logger.info("创建无痕浏览器…（剩余 {}）", len(remaining_urls))
                browser = AutoBrowseService(incognito=True, headless=False, enable_turnstile_bypass=True)

                logger.info("开始登录…")
                await login_pastebin(
                    browser,
                    password=pastebin_password,
                    accounts=pastebin_accounts,
                )

                processed_urls: List[str] = []
                for search_url in remaining_urls:
                    try:
                        links = await process_search_url(browser, search_url)

                        if links == "limit_reached":
                            if browser:
                                try:
                                    browser.close()
                                    browser = None
                                except Exception:
                                    pass
                            await asyncio.sleep(3)
                            break

                        if isinstance(links, list):
                            if links:
                                new_links = await filter_new_urls_local(links, existing_urls)
                                dup = len(links) - len(new_links)
                                if dup > 0:
                                    logger.info("过滤掉 {} 个重复 URL", dup)
                                if new_links:
                                    all_links.extend(new_links)
                                    existing_urls.update(new_links)
                                    logger.info(
                                        "{} 完成，{} 个新链接（共 {}）",
                                        search_url,
                                        len(new_links),
                                        len(links),
                                    )
                                else:
                                    logger.info("{} 共 {} 个链接均为重复", search_url, len(links))
                            else:
                                logger.info("{} 无搜索结果", search_url)
                            processed_urls.append(search_url)

                    except Exception as e:
                        logger.error("处理 {} 出错: {}", search_url, e)
                        await asyncio.sleep(3)
                        break

                for url in processed_urls:
                    if url in remaining_urls:
                        remaining_urls.remove(url)

                if remaining_urls:
                    completed = total_urls - len(remaining_urls)
                    pct = completed * 100 // total_urls if total_urls else 0
                    logger.info("进度: {}/{} ({}%)", completed, total_urls, pct)
                    await asyncio.sleep(1)
                else:
                    logger.info("所有搜索 URL 已处理完毕")
                    break

            except Exception as e:
                logger.error("浏览器流程出错: {}", e)
                if browser:
                    try:
                        browser.close()
                        browser = None
                    except Exception:
                        pass
                await asyncio.sleep(2)

        if not all_links:
            logger.warning("未收集到任何链接")
            return []

        save_result = await batch_save_urls_sqlite(sqlite_path, all_links, source="pastebin")
        logger.info(
            "URL 已写入 SQLite: 新插入 {}, 跳过重复批次内 {}",
            save_result["success"],
            save_result["duplicate"],
        )

        history_urls = load_recent_scan_history_targets(
            source="pastebin",
            match_type=SCAN_HISTORY_MATCH_TARGET,
            window_days=SCAN_HISTORY_WINDOW_DAYS,
        )
        scan_links = [url for url in all_links if url.strip() and url.strip() not in history_urls]
        logger.info(
            "按 {} 天历史表（URL）过滤: 跳过 {} 个重复链接，剩余 {} 个待扫描（历史 URL {}）",
            SCAN_HISTORY_WINDOW_DAYS,
            len(all_links) - len(scan_links),
            len(scan_links),
            len(history_urls),
        )
        if not scan_links:
            logger.warning("过滤后命中历史表，无新链接可扫")
            return []

        logger.info("开始扫描 {} 个链接…", len(scan_links))
        scan_browser = AutoBrowseService(enable_turnstile_bypass=True, incognito=True)
        try:
            scan_results = await scan_pastebin_urls(
                scan_links,
                concurrent=scan_concurrent,
                browser=scan_browser,
            )
        finally:
            try:
                scan_browser.close()
            except Exception:
                pass

        saved_history = save_scan_history(scan_links, source="pastebin", match_type=SCAN_HISTORY_MATCH_TARGET)
        logger.info("扫描历史已写入 {} 个链接（保留 {} 天）", saved_history, SCAN_HISTORY_WINDOW_DAYS)

        if scan_results:
            ensure_parent_dir(keys_json_path)
            Path(keys_json_path).write_text(
                json.dumps(scan_results, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            logger.info("已将 {} 条密钥记录写入 {}", len(scan_results), keys_json_path)

        if verify and scan_results:
            from app.services import key_service
            from app.utils.status_summary import count_status_codes, format_status_code_counts

            logger.info("开始批量校验 {} 个密钥…", len(scan_results))
            results = await key_service.batch_process_keys(
                [{"key": item["key"], "origin": item["url"]} for item in scan_results],
                concurrent=verify_concurrent,
            )
            saved = sum(1 for r in results if r["saved"])
            logger.success(
                "\n{}\n密钥入库统计: 总计 {} | 成功 {} | 失败 {}\n{}",
                "=" * 55,
                len(results),
                saved,
                len(results) - saved,
                "=" * 55,
            )
            logger.info("校验状态码统计: {}", format_status_code_counts(count_status_codes(results)))

        return scan_results

    finally:
        if browser:
            try:
                browser.close()
            except Exception:
                pass
        await close_http_client()
        logger.info("资源清理完成")
