import aiohttp

TEST_MODEL = "grok-4-latest"
CHAT_URL = "https://api.x.ai/v1/chat/completions"
MODELS_URL = "https://api.x.ai/v1/models"
_TIMEOUT = aiohttp.ClientTimeout(total=120)


class XAIService:
    @staticmethod
    async def verify(api_key: str, model=None) -> dict:
        """
        通过官方 Chat Completions API 校验 xAI key。
        未指定模型时默认调用 Grok，返回 {status_code, tier, body}。
        """
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model or TEST_MODEL,
            "messages": [{"role": "user", "content": "say hello to me"}],
            "max_tokens": 1,
        }
        async with (
            aiohttp.ClientSession(timeout=_TIMEOUT) as session,
            session.post(CHAT_URL, headers=headers, json=payload) as resp,
        ):
            try:
                body = await resp.json()
            except Exception:
                body = await resp.text()
            return {"status_code": resp.status, "tier": None, "body": body}

    @staticmethod
    async def fetch_models(api_key: str) -> list[str]:
        """获取该 xAI key 可用的模型列表。"""
        headers = {"Authorization": f"Bearer {api_key}"}
        try:
            async with (
                aiohttp.ClientSession(timeout=_TIMEOUT) as session,
                session.get(MODELS_URL, headers=headers) as resp,
            ):
                if resp.status == 200:
                    data = await resp.json()
                    return sorted(
                        item["id"] for item in data.get("data", []) if item.get("id")
                    )
        except Exception:
            pass
        return []

    @staticmethod
    async def batch_verify(api_keys: list[str], concurrent: int = 20) -> list[dict]:
        from app.utils.concurrency import gather_limited

        async def _one(key: str) -> dict:
            return {"key": key, **(await XAIService.verify(key))}

        return await gather_limited([_one(k) for k in api_keys], concurrent=concurrent)
