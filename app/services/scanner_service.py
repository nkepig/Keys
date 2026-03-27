import aiohttp
from loguru import logger

from app.http_client import get_http_session
from app.services.key_service import detect_all_keys_from_text
from app.utils.concurrency import gather_limited

_FETCH_TIMEOUT = aiohttp.ClientTimeout(total=15)
_USER_AGENT = "Mozilla/5.0 (compatible; KeyScanner/1.0)"


async def _fetch(host: str) -> tuple[str, str]:
    """尝试 https 再 http，返回 (最终url, 页面文本)。失败时返回空文本。"""
    session = get_http_session()
    headers = {"User-Agent": _USER_AGENT}
    url = host if host.startswith("http") else f"https://{host}"
    fallback = host if host.startswith("http") else f"http://{host}"
    for target in (url, fallback):
        try:
            async with session.get(
                target,
                headers=headers,
                timeout=_FETCH_TIMEOUT,
                allow_redirects=True,
                ssl=False,
            ) as resp:
                if resp.status == 200:
                    return target, await resp.text(errors="ignore")
        except Exception:
            pass
    return host, ""


async def scan_urls(hosts: list[str], concurrent: int = 40) -> list[dict]:
    """
    并发抓取 hosts 列表的页面内容，提取全部匹配到的 API 密钥。
    返回去重后的 list[{provider, key, url}]。
    """
    async def _one(host: str) -> list[dict]:
        url, text = await _fetch(host)
        if not text:
            return []
        found = [{"provider": p, "key": k, "url": url} for p, k in detect_all_keys_from_text(text)]
        if found:
            logger.debug(f"[{host}] 发现 {len(found)} 个密钥")
        return found

    nested = await gather_limited([_one(h) for h in hosts], concurrent=concurrent)

    seen: set[str] = set()
    all_keys: list[dict] = []
    for items in nested:
        for item in items:
            if item["key"] not in seen:
                seen.add(item["key"])
                all_keys.append(item)

    logger.info(f"扫描完成：{len(hosts)} 个 URL，找到 {len(all_keys)} 个唯一密钥")
    return all_keys
