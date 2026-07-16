import base64
import json
import os
import time
from pathlib import Path

from aiohttp import ClientResponseError
from app.http_client import get_http_session
from loguru import logger

_LOGIN_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json",
}


def _jwt_exp(token: str) -> float | None:
    parts = token.split(".")
    if len(parts) < 2:
        return None
    seg = parts[1]
    pad = "=" * (-len(seg) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(seg + pad))
        exp = payload.get("exp")
        return float(exp) if exp is not None else None
    except Exception:
        return None


class YunwuService:
    """与云雾供应商 API 交互：JWT 登录缓存 + 模型实时缺口查询。"""

    def __init__(self, base_url: str, username: str, password: str, gap_path: str):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.gap_path = gap_path
        self._token: str | None = None

    def _cache_file(self) -> Path:
        host = self.base_url.removeprefix("http://").removeprefix("https://").rstrip("/").translate(str.maketrans(":/", "__"))
        return Path(__file__).resolve().parents[2] / "data" / f"yunwu_auth_{host}.json"

    async def _token_for_request(self, refresh: bool = False) -> str:
        if self._token and not refresh:
            return self._token

        use_cache = os.getenv("YUNWU_AUTH_CACHE", "1").strip().lower() not in ("0", "false", "no")
        cache_file = self._cache_file()
        skew = float(os.getenv("YUNWU_AUTH_REFRESH_SKEW", "60"))

        if use_cache and not refresh and cache_file.is_file():
            try:
                obj = json.loads(cache_file.read_text(encoding="utf-8"))
                exp = float(obj.get("exp", 0)) if obj.get("exp") else None
                fresh = exp is None or time.time() < exp - skew
                if fresh and obj.get("token"):
                    self._token = obj["token"]
                    return self._token
            except Exception:
                pass

        logger.info("yunwu token 更新中（refresh={}）", refresh)
        session = get_http_session()
        async with session.post(
            f"{self.base_url}/api/auth/login",
            headers=_LOGIN_HEADERS,
            json={"username": self.username, "password": self.password},
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
        token = data.get("data", {}).get("token") if isinstance(data, dict) else None
        if not token:
            raise ValueError("云雾登录未返回 token，请检查账号信息")
        self._token = token

        if use_cache:
            exp = _jwt_exp(token)
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(
                json.dumps({"token": token, "exp": exp, "saved_at": time.time()}, ensure_ascii=False),
                encoding="utf-8",
            )
        return self._token

    async def _request(self, method: str, path: str, *, params: dict | None = None) -> dict:
        session = get_http_session()
        for refresh in (False, True):
            try:
                token = await self._token_for_request(refresh=refresh)
                async with session.request(
                    method,
                    f"{self.base_url}{path}",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/json, text/plain, */*",
                    },
                    params=params,
                ) as resp:
                    resp.raise_for_status()
                    body = await resp.json()
                    return body.get("data", body) if isinstance(body, dict) else body
            except ClientResponseError as e:
                if e.status in (401, 403) and not refresh:
                    logger.warning("yunwu 鉴权失效，刷新 token 后重试: {} {}", method, path)
                    continue
                if e.status == 429:
                    logger.warning("yunwu 请求被限流(429): {} {}", method, path)
                raise
            except Exception:
                if not refresh:
                    logger.warning("yunwu 请求异常，刷新 token 后重试: {} {}", method, path)
                    continue
                raise
        raise RuntimeError("yunwu 请求失败")

    async def get_model_gap(self) -> list[dict]:
        data = await self._request("GET", self.gap_path)
        if isinstance(data, list):
            return data
        return []


_service: YunwuService | None = None


def get_service() -> YunwuService:
    global _service
    if _service is None:
        from app.config import settings

        _service = YunwuService(
            base_url=settings.yunwu_base_url,
            username=settings.yunwu_username,
            password=settings.yunwu_password,
            gap_path=settings.yunwu_gap_path,
        )
    return _service