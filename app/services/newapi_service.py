from dataclasses import dataclass

from app.http_client import get_http_session


@dataclass
class AuthContext:
    headers: dict
    user: dict


class NewAPIService:
    """与 New API 官站 HTTP API 交互。"""

    def __init__(self, base_url: str, username: str, password: str):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self._session = None
        self._auth_context: AuthContext | None = None

    async def _get_session(self):
        if self._session is None:
            self._session = get_http_session()
        return self._session

    async def _ensure_auth_context(self):
        if self._auth_context is None:
            self._auth_context = await self.build_auth_context()
        return self._auth_context

    async def build_auth_context(self) -> AuthContext:
        session = await self._get_session()

        async with session.post(
            f"{self.base_url}/api/user/login",
            json={"username": self.username, "password": self.password},
        ) as response:
            response.raise_for_status()

            response_data = await response.json()
            user_info = response_data.get("data") if isinstance(response_data, dict) else response_data

            cookies_list: list[str] = []
            if response.cookies:
                for cookie in response.cookies.values():
                    cookies_list.append(f"{cookie.key}={cookie.value}")
            if not cookies_list:
                for _domain, paths in session.cookie_jar._cookies.items():
                    for _path, cookie_dict in paths.items():
                        for name, morsel in cookie_dict.items():
                            cookies_list.append(f"{name}={morsel.value}")
            if not cookies_list:
                for h in response.headers.getall("Set-Cookie", []) or [response.headers.get("Set-Cookie", "")]:
                    if h:
                        part = h.split(";")[0].strip()
                        if "=" in part:
                            cookies_list.append(part)

            cookie_str = "; ".join(cookies_list)

        if not cookie_str or not user_info:
            raise ValueError("无法构建认证上下文，请检查账号信息")

        headers = {
            "accept": "application/json",
            "cookie": cookie_str,
            "new-api-user": str(user_info.get("id")),
        }
        return AuthContext(headers=headers, user=user_info)

    async def delete_channel(self, channel_id: int) -> dict:
        session = await self._get_session()
        auth_context = await self._ensure_auth_context()
        headers = {**auth_context.headers, "accept": "application/json, text/plain, */*"}
        async with session.delete(f"{self.base_url}/api/channel/{channel_id}", headers=headers) as response:
            response.raise_for_status()
            return await response.json()

    async def get_user_logs(self, page: int = 1, page_size: int = 20) -> dict:
        session = await self._get_session()
        auth = await self._ensure_auth_context()
        headers = {**auth.headers, "accept": "application/json, text/plain, */*", "cache-control": "no-store"}
        params = {"p": page, "page_size": page_size, "type": 0}

        async with session.get(f"{self.base_url}/api/log/", headers=headers, params=params) as response:
            response.raise_for_status()
            result = await response.json()
            return result.get("data", result)

    async def get_users(self, page: int = 1, page_size: int = 100) -> dict:
        session = await self._get_session()
        auth = await self._ensure_auth_context()
        headers = {
            **auth.headers,
            "accept": "application/json, text/plain, */*",
            "cache-control": "no-store",
        }
        params = {"p": page, "page_size": page_size}
        async with session.get(f"{self.base_url}/api/user/", headers=headers, params=params) as response:
            response.raise_for_status()
            result = await response.json()
            return result.get("data", result)
