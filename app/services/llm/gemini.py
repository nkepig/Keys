import aiohttp

TEST_MODEL = "gemini-3-flash-preview"
_TIMEOUT = aiohttp.ClientTimeout(total=120)


def _generate_url(model: str) -> str:
    return f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


class GeminiService:
    @staticmethod
    async def verify(api_key: str, model: str | None = None) -> dict:
        """
        通过官方 REST 校验 Gemini key，返回 {"status_code": int, "tier": None}
        状态码与 Google API HTTP 响应一致。
        """
        url = _generate_url(model or TEST_MODEL)
        params = {"key": api_key}
        payload = {"contents": [{"parts": [{"text": "1"}]}]}
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
            async with session.post(url, params=params, json=payload) as resp:
                try:
                    body = await resp.json()
                except Exception:
                    body = await resp.text()
                return {"status_code": resp.status, "tier": None, "body": body}

    @staticmethod
    async def fetch_models(api_key: str) -> list[str]:
        """获取 Gemini 支持 generateContent 的模型列表"""
        try:
            async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
                async with session.get(
                    "https://generativelanguage.googleapis.com/v1beta/models",
                    params={"key": api_key},
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return sorted([
                            item["name"].split("/")[-1]
                            for item in data.get("models", [])
                            if "generateContent" in item.get("supportedGenerationMethods", [])
                        ])
        except Exception:
            pass
        return []

    @staticmethod
    async def batch_verify(api_keys: list[str], concurrent: int = 20) -> list[dict]:
        from app.utils.concurrency import gather_limited

        async def _one(key: str) -> dict:
            return {"key": key, **(await GeminiService.verify(key))}

        return await gather_limited([_one(k) for k in api_keys], concurrent=concurrent)
