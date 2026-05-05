import json
import os
import time
from pathlib import Path

from aiohttp import ClientResponseError
from app.http_client import get_http_session
from loguru import logger


class NewAPIService:
    """与 New API 官站 HTTP API 交互。"""

    def __init__(self, base_url: str, username: str, password: str):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self._hdrs: dict | None = None

    async def _header(self, refresh: bool = False) -> dict:
        if self._hdrs and not refresh:
            return self._hdrs

        host = self.base_url.removeprefix("http://").removeprefix("https://").rstrip("/").translate(str.maketrans(":/", "__"))
        cache_file = Path(__file__).resolve().parents[2] / "data" / f"newapi_auth_{host}.json"
        use_cache = os.getenv("NEWAPI_AUTH_CACHE", "1").strip().lower() not in ("0", "false", "no")
        ttl = float(os.getenv("NEWAPI_AUTH_CACHE_TTL", "3600"))

        if use_cache and not refresh and cache_file.is_file():
            try:
                obj = json.loads(cache_file.read_text(encoding="utf-8"))
                if time.time() - float(obj.get("saved_at", 0)) <= ttl and isinstance(obj.get("headers"), dict):
                    self._hdrs = obj["headers"]
                    return self._hdrs
            except Exception:
                pass

        logger.info("newapi header 更新中（refresh={}）", refresh)
        session = get_http_session()
        async with session.post(
            f"{self.base_url}/api/user/login",
            json={"username": self.username, "password": self.password},
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
            user = data.get("data") if isinstance(data, dict) else data
            cookies: list[str] = [f"{c.key}={c.value}" for c in resp.cookies.values()] if resp.cookies else []
            if not cookies:
                for domain in session.cookie_jar._cookies.values():
                    for cookie_dict in domain.values():
                        cookies.extend(f"{k}={v.value}" for k, v in cookie_dict.items())
            if not cookies:
                for line in resp.headers.getall("Set-Cookie", []) or [resp.headers.get("Set-Cookie", "")]:
                    if line and "=" in (part := line.split(";")[0].strip()):
                        cookies.append(part)

        cookie = "; ".join(cookies)
        if not cookie or not user:
            raise ValueError("无法构建认证上下文，请检查账号信息")

        self._hdrs = {"accept": "application/json", "cookie": cookie, "new-api-user": str(user.get("id"))}
        if use_cache:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps({"saved_at": time.time(), "headers": self._hdrs}, ensure_ascii=False), encoding="utf-8")
        return self._hdrs

    async def _request(self, method: str, path: str, *, headers: dict | None = None, params: dict | None = None, unwrap_data: bool = True) -> dict:
        session = get_http_session()
        extra_headers = headers or {}
        for refresh in (False, True):
            try:
                merged_headers = {**(await self._header(refresh=refresh)), **extra_headers}
                async with session.request(method, f"{self.base_url}{path}", headers=merged_headers, params=params) as resp:
                    resp.raise_for_status()
                    body = await resp.json()
                    return body.get("data", body) if unwrap_data else body
            except ClientResponseError as e:
                # 仅在鉴权失效场景刷新 header；限流/其他错误不做二次登录重试
                if e.status in (401, 403) and not refresh:
                    logger.warning("newapi 鉴权可能失效，刷新 header 后重试: {} {}", method, path)
                    continue
                if e.status == 429:
                    logger.warning("newapi 登录/请求被限流(429)，不触发 header 刷新重试: {} {}", method, path)
                raise
            except Exception:
                if not refresh:
                    logger.warning("newapi 请求异常，刷新 header 后重试: {} {}", method, path)
                    continue
                raise

        raise RuntimeError("请求失败")

    async def delete_channel(self, channel_id: int) -> dict:
        return await self._request(
            "DELETE",
            f"/api/channel/{channel_id}",
            headers={"accept": "application/json, text/plain, */*"},
            unwrap_data=False,
        )

    async def get_user_logs(self, page: int = 1, page_size: int = 20) -> dict:
        return await self._request(
            "GET",
            "/api/log/",
            headers={"accept": "application/json, text/plain, */*", "cache-control": "no-store"},
            params={"p": page, "page_size": page_size, "type": 0},
        )

    async def get_users(self, page: int = 1, page_size: int = 100) -> dict:
        return await self._request(
            "GET",
            "/api/user/",
            headers={"accept": "application/json, text/plain, */*", "cache-control": "no-store"},
            params={"p": page, "page_size": page_size},
        )
