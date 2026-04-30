#!/usr/bin/env python3
"""
Pastebin 搜索 → 浏览器扫页 → 密钥提取 → 批量校验入库。

在项目根目录执行（需已激活 venv）:

    python scripts/pastebin_scraper.py

账号、密码、关键词等均在下方常量中修改；无命令行参数。
"""
from __future__ import annotations

import asyncio
import random
import sys
from pathlib import Path
from typing import Optional

from loguru import logger

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from app.db import init_db
from app.http_client import close_http_client
from app.services import key_service
from app.services.browser_service import AutoBrowseService
from app.services.key_service import detect_all_keys_from_text
from app.utils.scan_history import (
    SCAN_HISTORY_MATCH_TARGET,
    SCAN_HISTORY_WINDOW_DAYS,
    load_recent_scan_history_targets,
    save_scan_history,
)
from app.utils.status_summary import count_status_codes, format_status_code_counts

PASTEBIN_PASSWORD = "abcd12345678"

PASTEBIN_ACCOUNTS = [
    "1775617332",
    "1775617354",
    "1775617381",
    "1775617416",
    "1775617437",
]

QUERIES = ["sk-", "openai", "api_key", "AIzaSy", "gemini", "sk-ant-api03-"]

VERIFY = True
SCAN_CONCURRENT = 2
VERIFY_CONCURRENT = 40

BASE_URL = "https://pastebin.com"
SEARCH_URL = f"{BASE_URL}/search"
DEFAULT_QUERIES = ["sk-", "openai", "api_key", "AIzaSy", "gemini"]
BROWSER_PATH = None


def pick_account(accounts: list[str]) -> Optional[str]:
    cleaned = [u.strip() for u in accounts if u and str(u).strip()]
    return random.choice(cleaned) if cleaned else None


def generate_search_urls(queries: list[str]) -> list[str]:
    search_urls: list[str] = []
    for query in queries:
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


async def fetch_html_with_browser(browser: AutoBrowseService, url: str) -> str | None:
    loop = asyncio.get_event_loop()
    tab = None
    try:
        tab = await loop.run_in_executor(None, browser.browser.new_tab)
        await loop.run_in_executor(None, tab.get, url)
        try:
            await loop.run_in_executor(None, lambda: tab.wait.load_start(timeout=10))
        except Exception:
            pass
        await asyncio.sleep(2)
        wait_count = 0
        while wait_count < 10:
            try:
                ready_state = await loop.run_in_executor(None, lambda: tab.run_js("return document.readyState"))
                if ready_state == "complete":
                    break
                await asyncio.sleep(0.5)
                wait_count += 1
            except Exception:
                break
        if "kaggle.com" in url:
            try:
                iframe = await loop.run_in_executor(None, lambda: tab.get_frame("t:iframe", timeout=10))
                if iframe:
                    return await loop.run_in_executor(None, lambda: iframe.html)
            except Exception:
                pass
        return await loop.run_in_executor(None, lambda: tab.html)
    except Exception as e:
        logger.error("扫描 URL {} 时出错: {}", url, e)
        return None
    finally:
        if tab:
            try:
                await loop.run_in_executor(None, tab.close)
            except Exception:
                pass


async def scan_pastebin_urls(
    urls: list[str],
    *,
    concurrent: int,
    browser: AutoBrowseService,
) -> list[dict]:
    total = len(urls)
    logger.info("开始扫描 {} 个 Pastebin URL，并发数: {}", total, concurrent)
    semaphore = asyncio.Semaphore(concurrent)
    completed_count = 0
    all_keys: list[dict] = []

    async def _one(url: str) -> list[dict]:
        nonlocal completed_count
        async with semaphore:
            await asyncio.sleep(0.1)
            html = await fetch_html_with_browser(browser, url)
            completed_count += 1
            step = max(1, total // 10)
            if completed_count % step == 0 or completed_count == total:
                logger.info("扫描进度: {}/{}", completed_count, total)
            if not html:
                return []
            found: list[dict] = []
            for provider, key in detect_all_keys_from_text(html):
                logger.info("发现密钥 {}: {}... <- {}", provider, key[:20], url)
                found.append({"provider": provider, "key": key, "url": url})
            if found:
                print(url, flush=True)
            return found

    tasks = [asyncio.create_task(_one(u)) for u in urls]
    for coro in asyncio.as_completed(tasks):
        try:
            result = await coro
            if result:
                all_keys.extend(result)
        except Exception as e:
            logger.debug("任务异常: {}", e)

    logger.info("扫描完成，共 {} 条密钥记录（含同 key 多 URL）", len(all_keys))
    return all_keys


async def run_pastebin_scrape(
    *,
    pastebin_password: str,
    pastebin_accounts: list[str],
    queries: list[str] | None = None,
    scan_concurrent: int = 2,
    verify_concurrent: int = 40,
    verify: bool = True,
    browser_path: str = None,
) -> list[dict]:
    init_db()

    qlist = queries if queries else DEFAULT_QUERIES

    all_links: list[str] = []
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

                logger.info("创建无痕浏览器（剩余 {}）…", len(remaining_urls))
                browser = AutoBrowseService(
                    incognito=True,
                    headless=False,
                    enable_turnstile_bypass=True,
                    browser_path=browser_path,
                )

                logger.info("开始登录…")
                await login_pastebin(
                    browser,
                    password=pastebin_password,
                    accounts=pastebin_accounts,
                )

                processed_urls: list[str] = []
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
                                all_links.extend(links)
                                logger.info("{} 完成，{} 个链接", search_url, len(links))
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

        for url in all_links:
            print(url, flush=True)

        history_urls = load_recent_scan_history_targets(
            source="pastebin",
            match_type=SCAN_HISTORY_MATCH_TARGET,
            window_days=SCAN_HISTORY_WINDOW_DAYS,
        )
        scan_links = [url for url in all_links if url.strip() and url.strip() not in history_urls]
        logger.info(
            "按 {} 天历史表过滤: 跳过 {} 个重复，剩余 {} 个待扫描",
            SCAN_HISTORY_WINDOW_DAYS,
            len(all_links) - len(scan_links),
            len(scan_links),
        )
        if not scan_links:
            logger.warning("过滤后无新链接可扫")
            return []

        logger.info("开始扫描 {} 个链接…", len(scan_links))
        scan_browser = AutoBrowseService(
            enable_turnstile_bypass=True,
            incognito=True,
            browser_path=browser_path,
        )
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
            logger.info("发现 {} 条密钥记录", len(scan_results))

        if verify and scan_results:
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


async def main() -> None:
    logger.remove()
    logger.add(sys.stderr, level="ERROR")

    try:
        scan_results = await run_pastebin_scrape(
            pastebin_password=PASTEBIN_PASSWORD,
            pastebin_accounts=PASTEBIN_ACCOUNTS,
            queries=QUERIES,
            scan_concurrent=SCAN_CONCURRENT,
            verify_concurrent=VERIFY_CONCURRENT,
            verify=VERIFY,
            browser_path=BROWSER_PATH,
        )
        if not scan_results:
            logger.warning("未发现任何密钥或未收集到链接")
    except Exception as e:
        logger.exception("程序异常: {}", e)
        raise


if __name__ == "__main__":
    asyncio.run(main())
