from typing import Final, TypedDict

import anyio
from aiohttp import ClientError

from app.config import settings
from app.http_client import get_http_session
from app.utils.timezone import china_now

CATEGORIES: Final = {
    "anthropic": "Anthropic",
    "anthropic_small": "Anthropic（小额度）",
    "openai": "OpenAI",
    "aws": "AWS",
    "azure": "Azure",
    "ai_studio": "AI Studio",
}
BATCH_SIZE: Final = 2000
MAX_RETRIES: Final = 3
TIMEOUT: Final = 60.0


class UpstreamData(TypedDict, total=False):
    success: int
    skipped_dup: int
    failed: int
    invalid: int


class UpstreamResponse(TypedDict, total=False):
    code: int
    request_id: str
    data: UpstreamData


class UploadSuccess(TypedDict):
    ok: bool
    tag: str
    total: int
    success: int
    skipped: int
    invalid: int
    failed: int


class UploadFailure(TypedDict):
    ok: bool
    error: str


class UploadPermissionError(PermissionError):
    pass


class UploadParameterError(ValueError):
    pass


class UploadServiceError(RuntimeError):
    pass


def validate_key(key: str, category: str) -> bool:
    if category in ("anthropic", "anthropic_small"):
        return key.startswith("sk-ant-")
    if category == "openai":
        return key.startswith("sk-") and not key.startswith("sk-ant-")
    if category in ("aws", "azure"):
        parts = key.split("|")
        expected = 2 if category == "aws" else 3
        return len(parts) == expected and all(part.strip() for part in parts)
    if category == "ai_studio":
        return bool(key)
    return False


def clean_keys(text: str, category: str) -> tuple[list[str], int]:
    keys: list[str] = []
    seen: set[str] = set()
    invalid = 0
    for line in text.splitlines():
        key = line.strip()
        if not key:
            continue
        if not validate_key(key, category):
            invalid += 1
            continue
        if key not in seen:
            seen.add(key)
            keys.append(key)
    return keys, invalid


def make_batch_tag() -> str:
    return china_now().strftime("%Y%m%d-%H%M%S")


async def post_batch(tag: str, keys: list[str], category: str) -> UpstreamResponse:
    session = get_http_session()
    headers = {
        "Authorization": f"Bearer {settings.msk_api_key}",
        "Content-Type": "application/json",
    }
    payload = {"category": category, "tag": tag, "keys": keys}
    last_error = "服务暂时不可用"

    for attempt in range(MAX_RETRIES + 1):
        try:
            async with session.post(
                f"{settings.msk_base_url.rstrip('/')}/channels",
                headers=headers,
                json=payload,
                timeout=TIMEOUT,
            ) as response:
                body = await response.json()
                status = response.status
        except (ClientError, TimeoutError, ValueError) as exc:
            last_error = f"网络错误: {exc}"
            if attempt < MAX_RETRIES:
                await anyio.sleep(2 ** (attempt + 1))
                continue
            raise UploadServiceError(last_error) from exc

        code = body.get("code", -1)
        request_id = body.get("request_id", "-")
        if code == 0:
            return body
        if code == 40101:
            raise UploadPermissionError(f"API Key 无效或停用（{request_id}）")
        if code == 40301:
            raise UploadPermissionError(f"权限不足（{request_id}）")
        if code == 40001:
            raise UploadParameterError(f"上传参数错误（{request_id}）")
        if code == 50001 or status >= 500:
            last_error = f"服务端错误（{request_id}）"
        else:
            raise UploadServiceError(last_error)

        if attempt < MAX_RETRIES:
            await anyio.sleep(2 ** (attempt + 1))
            continue
        raise UploadServiceError(last_error)

    raise UploadServiceError(last_error)


async def upload_keys(text: str, category: str) -> UploadSuccess | UploadFailure:
    keys, local_invalid = clean_keys(text, category)
    if not keys:
        label = CATEGORIES.get(category, category)
        return {"ok": False, "error": f"请输入有效的 {label} Key"}

    tag = make_batch_tag()
    success = 0
    skipped = 0
    failed = 0
    api_invalid = 0
    for start in range(0, len(keys), BATCH_SIZE):
        result = await post_batch(tag, keys[start : start + BATCH_SIZE], category)
        data = result.get("data", {})
        success += data.get("success", 0)
        skipped += data.get("skipped_dup", 0)
        failed += data.get("failed", 0)
        api_invalid += data.get("invalid", 0)

    return {
        "ok": True,
        "tag": tag,
        "total": len(keys),
        "success": success,
        "skipped": skipped,
        "invalid": local_invalid + api_invalid,
        "failed": failed,
    }
