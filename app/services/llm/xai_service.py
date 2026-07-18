from typing import Any

from app.http_client import get_http_session

MODELS_URL = "https://api.x.ai/v1/models"
CHAT_URL = "https://api.x.ai/v1/chat/completions"


async def _response_body(response: Any) -> Any:
    try:
        return await response.json()
    except Exception:
        return await response.text()


class XAIService:
    @staticmethod
    async def verify(api_key: str, model=None) -> dict:
        """校验 xAI/Grok API key，并透传官方 API 的响应体。"""
        headers = {"Authorization": f"Bearer {api_key}"}
        session = get_http_session()

        if model:
            payload = {
                "model": model,
                "messages": [{"role": "user", "content": "say hi"}],
                "max_tokens": 1,
            }
            async with session.post(
                CHAT_URL,
                headers={**headers, "Content-Type": "application/json"},
                json=payload,
            ) as response:
                return {
                    "status_code": response.status,
                    "tier": None,
                    "body": await _response_body(response),
                }

        # 模型列表接口不消耗推理额度，也能验证 key 是否有效。
        async with session.get(MODELS_URL, headers=headers) as response:
            return {
                "status_code": response.status,
                "tier": None,
                "body": await _response_body(response),
            }

    @staticmethod
    async def fetch_models(api_key: str) -> list[str]:
        """获取当前 xAI API key 可访问的模型。"""
        headers = {"Authorization": f"Bearer {api_key}"}
        try:
            session = get_http_session()
            async with session.get(MODELS_URL, headers=headers) as response:
                if response.status != 200:
                    return []
                body = await response.json()
                return sorted(
                    item["id"]
                    for item in body.get("data", [])
                    if isinstance(item, dict) and item.get("id")
                )
        except Exception:
            return []

    @staticmethod
    async def batch_verify(api_keys: list[str], concurrent: int = 20) -> list[dict]:
        from app.utils.concurrency import gather_limited

        async def _one(key: str) -> dict:
            return {"key": key, **(await XAIService.verify(key))}

        return await gather_limited(
            [_one(key) for key in api_keys], concurrent=concurrent
        )
