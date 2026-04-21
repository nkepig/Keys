import aiohttp

CREDITS_URL = "https://openrouter.ai/api/v1/credits"
_TIMEOUT = aiohttp.ClientTimeout(total=120)


class OpenRouterService:
    @staticmethod
    async def verify(api_key: str, model: str | None = None) -> dict:
        """
        校验 OpenRouter key，返回 {"status_code": int, "tier": float | None}

        状态码规则：
          - 余额为负 → 429
          - 无效 key  → 401
          - 正常       → 200
        tier = 剩余余额（total_credits - total_usage），保留两位小数
        """
        headers = {"Authorization": f"Bearer {api_key}"}
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
            async with session.get(CREDITS_URL, headers=headers) as resp:
                sc = resp.status
                try:
                    body = await resp.json()
                except Exception:
                    body = await resp.text()
                if sc != 200:
                    return {"status_code": sc, "tier": None, "body": body}

                data = body if isinstance(body, dict) else {}
                total = data.get("data", {}).get("total_credits", 0)
                usage = data.get("data", {}).get("total_usage", 0)
                remaining = total - usage

                tier_val = round(remaining, 2)

                if remaining <= 0:
                    return {"status_code": 429, "tier": tier_val, "body": body}

                return {"status_code": 200, "tier": tier_val, "body": body}

    @staticmethod
    async def batch_verify(api_keys: list[str], concurrent: int = 20) -> list[dict]:
        from app.utils.concurrency import gather_limited

        async def _one(key: str) -> dict:
            return {"key": key, **(await OpenRouterService.verify(key))}

        return await gather_limited([_one(k) for k in api_keys], concurrent=concurrent)
