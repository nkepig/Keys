"""用浏览器拉取页面 HTML，与项目 key_service 规则一致提取密钥。"""
from __future__ import annotations

import asyncio

from loguru import logger

from app.pastebin.browser import AutoBrowseService
from app.services.key_service import detect_all_keys_from_text


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
    """并发用浏览器打开 URL，提取密钥（与全局 REGEX_RULES 一致）。"""
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
            # Emit the URL to stdout when keys are discovered on this URL.
            # This keeps the behavior minimal and avoids altering JSON/db outputs.
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
