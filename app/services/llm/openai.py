import aiohttp

# gpt-5-mini token/min limit → tier
TIER_MAPPING = {
    500000: 1,
    2000000: 2,
    4000000: 3,
    10000000: 4,
    180000000: 5,
}
TEST_MODEL = "gpt-5.4-mini"
RATE_LIMIT_HEADER = "x-ratelimit-limit-tokens"
CHAT_URL = "https://api.openai.com/v1/responses"
_TIMEOUT = aiohttp.ClientTimeout(total=120)


class OpenAIService:
    @staticmethod
    async def verify(api_key: str, model: str | None = None) -> dict:
        """
        通过官方 HTTP API 校验 OpenAI key，返回 {"status_code": int, "tier": int | None}
        """
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model or TEST_MODEL,
            "messages": [{"role": "user", "content": "1"}],
            "max_tokens": 5,
        }
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
            async with session.post(CHAT_URL, headers=headers, json=payload) as resp:
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
        """获取该 key 可用的模型列表"""
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=api_key)
        try:
            result = await client.models.list()
            return sorted([m.id for m in result.data])
        except Exception:
            return []
        finally:
            await client.close()

    @staticmethod
    async def batch_verify(api_keys: list[str], concurrent: int = 20) -> list[dict]:
        from app.utils.concurrency import gather_limited

        async def _one(key: str) -> dict:
            return {"key": key, **(await OpenAIService.verify(key))}

        return await gather_limited([_one(k) for k in api_keys], concurrent=concurrent)
