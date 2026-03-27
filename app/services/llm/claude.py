import aiohttp

# claude input token/min limit → tier
TIER_MAPPING = {
    50000: 1,
    100000: 2,
    200000: 3,
    400000: 4,
}
TEST_MODEL = "claude-3-5-haiku-20241022"
RATE_LIMIT_HEADER = "anthropic-ratelimit-input-tokens-limit"
MESSAGES_URL = "https://api.anthropic.com/v1/messages"
_TIMEOUT = aiohttp.ClientTimeout(total=120)


class ClaudeService:
    @staticmethod
    async def verify(api_key: str) -> dict:
        """
        通过官方 HTTP API 校验 Claude key，返回 {"status_code": int, "tier": int | None}
        """
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        payload = {
            "model": TEST_MODEL,
            "max_tokens": 10,
            "messages": [{"role": "user", "content": "1"}],
        }
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
            async with session.post(MESSAGES_URL, headers=headers, json=payload) as resp:
                status_code = resp.status
                tier = None
                try:
                    body = await resp.json()
                except Exception:
                    body = await resp.text()
                if status_code == 200:
                    try:
                        tokens = int(resp.headers.get(RATE_LIMIT_HEADER) or 0)
                        tier = TIER_MAPPING.get(tokens)
                    except (TypeError, ValueError):
                        pass
                return {"status_code": status_code, "tier": tier, "body": body}

    @staticmethod
    async def fetch_models(api_key: str) -> list[str]:
        """获取 Anthropic 可用模型列表"""
        headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
        try:
            async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
                async with session.get("https://api.anthropic.com/v1/models", headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return sorted([item["id"] for item in data.get("data", []) if item.get("id")])
        except Exception:
            pass
        return []

    @staticmethod
    async def batch_verify(api_keys: list[str], concurrent: int = 20) -> list[dict]:
        from app.utils.concurrency import gather_limited

        async def _one(key: str) -> dict:
            return {"key": key, **(await ClaudeService.verify(key))}

        return await gather_limited([_one(k) for k in api_keys], concurrent=concurrent)
