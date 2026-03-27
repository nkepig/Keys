"""
Key CRUD 服务

设计原则：
- upsert_keys / delete_keys / update_keys 均接受 单个 dict/int 或 列表，
  内部用 asyncio.gather 并发执行，每个操作独享一个 Session（线程安全）。
- 所有公开函数均为 async，与 FastAPI 路由直接兼容。
"""
import asyncio
import json
import re

from sqlmodel import Session, select
from app.services.llm.openai import OpenAIService
from app.services.llm.claude import ClaudeService
from app.services.llm.gemini import GeminiService
from app.services.llm.openrouter import OpenRouterService
from app.db import engine
from app.models.key import Key
from app.utils.concurrency import gather_limited
# 供应商识别规则（按优先级匹配）
REGEX_RULES = {
    "OpenAI": [
        r"(?<![A-Za-z0-9_-])sk-proj-[A-Za-z0-9_-]{16,}(?![A-Za-z0-9_-])",
        r"(?<![A-Za-z0-9_-])sk-svcacct-[A-Za-z0-9_-]{16,}(?![A-Za-z0-9_-])",
        r"(?<![A-Za-z0-9_-])sk-[A-Za-z0-9]{24,}(?![A-Za-z0-9_-])",
    ],
    "Anthropic": [
        r"(?<![A-Za-z0-9_-])sk-ant-(?:api|sid)\d{2}-[A-Za-z0-9_-]{10,}(?![A-Za-z0-9_-])",
    ],
    "OpenRouter": [
        r"(?<![A-Za-z0-9_-])sk-or-v1-[A-Za-z0-9]{40,128}(?![A-Za-z0-9_-])",
    ],
    "xAI": [
        r"(?<![A-Za-z0-9_-])xai-[A-Za-z0-9]{32,}(?![A-Za-z0-9_-])",
    ],
    "Google": [
        r"(?<![A-Za-z0-9_-])AIza[a-zA-Z0-9_-]{35}(?![A-Za-z0-9_-])",
    ],
}

_COMPILED_RULES: list[tuple[str, re.Pattern[str]]] = [
    (provider, re.compile(pattern))
    for provider, patterns in REGEX_RULES.items()
    for pattern in patterns
]


def detect_provider_and_key(raw_text: str | None) -> tuple[str | None, str | None]:
    """
    从原始文本中识别供应商并提取 key。
    返回: (provider, key)，未匹配时返回 (None, None)。
    """
    if not raw_text:
        return None, None

    text = raw_text.strip()
    for provider, pattern in _COMPILED_RULES:
        match = pattern.search(text)
        if match:
            return provider, match.group(0)
    return None, None


def detect_all_keys_from_text(text: str) -> list[tuple[str, str]]:
    """
    在任意文本中提取全部 (provider, key) 对（不止第一个）。
    适用于批量页面扫描场景。
    """
    seen: set[str] = set()
    results: list[tuple[str, str]] = []
    for provider, pattern in _COMPILED_RULES:
        for m in pattern.finditer(text):
            key = m.group(0)
            if key not in seen:
                seen.add(key)
                results.append((provider, key))
    return results


async def _verify_dispatch(provider: str, key: str) -> dict:
    """根据供应商调用对应校验服务，返回 {status_code, tier}"""
    _services = {
        "OpenAI": OpenAIService,
        "Anthropic": ClaudeService,
        "Google": GeminiService,
        "OpenRouter": OpenRouterService,
    }
    service = _services.get(provider)
    if service is None:
        return {"status_code": None, "tier": None}
    try:
        return await service.verify(key)
    except Exception:
        # 校验函数本身只认 HTTP resp.status；无响应（网络/超时/解析失败）不伪造状态码
        return {"status_code": None, "tier": None}


async def fetch_models_for(provider: str, key: str) -> list[str]:
    """根据供应商获取支持的模型列表（status=200 时调用）"""

    _fetchers = {
        "OpenAI": OpenAIService.fetch_models,
        "Anthropic": ClaudeService.fetch_models,
        "Google": GeminiService.fetch_models,
    }
    fetcher = _fetchers.get(provider)
    if fetcher is None:
        return []
    try:
        return await fetcher(key)
    except Exception:
        return []


async def process_key(raw_key: str, origin: str | None = None) -> dict:
    """
    完整流程：识别供应商 → 校验 → 保存。
    """
    provider, clean_key = detect_provider_and_key(raw_key)

    if not clean_key or not provider:
        return {
            "id": None,
            "key": None,
            "provider": None,
            "status_code": None,
            "tier": None,
            "models_count": 0,
            "saved": False,
            "error": "无法识别供应商或 key",
        }

    verify_result = await _verify_dispatch(provider, clean_key)
    status_code = verify_result["status_code"]
    tier_val = str(verify_result["tier"]) if verify_result["tier"] is not None else None

    # 200 才获取模型列表
    models_json: str | None = None
    if status_code == 200:
        model_list = await fetch_models_for(provider, clean_key)
        if model_list:
            models_json = json.dumps(model_list, ensure_ascii=False)

    def _save() -> Key:
        with Session(engine) as session:
            obj = Key(
                provider=provider,
                key=clean_key,
                origin=origin,
                tier=tier_val,
                models=models_json,
                status_code=status_code,
            )
            session.add(obj)
            session.commit()
            session.refresh(obj)
            return obj

    saved = await asyncio.to_thread(_save)
    masked = f"{clean_key[:6]}•••{clean_key[-4:]}" if len(clean_key) > 10 else "••••••••"
    model_list = json.loads(models_json) if models_json else []
    return {
        "id": saved.id,
        "key": masked,
        "provider": provider,
        "status_code": status_code,
        "tier": tier_val,
        "models_count": len(model_list),
        "saved": True,
        "error": None,
    }


async def batch_process_keys(
    raw_text: str,
    origin: str | None = None,
    concurrent: int = 10,
) -> list[dict]:
    """
    批量流程：一行一个 key，自动识别 → 校验 → 保存。
    重复的行去重后并发处理。
    """
    lines = list(dict.fromkeys(
        line.strip() for line in raw_text.splitlines() if line.strip()
    ))
    if not lines:
        return []

    return await gather_limited(
        [process_key(line, origin) for line in lines],
        concurrent=concurrent,
    )


def _apply_provider_detection(data: dict) -> dict:
    """
    统一在上传/更新时执行 provider 识别。
    - 若识别到 key：写回标准化 key，并覆盖 provider。
    - 若识别不到：保留原值。
    """
    normalized = dict(data)
    provider, detected_key = detect_provider_and_key(normalized.get("key"))
    if detected_key:
        normalized["key"] = detected_key
    if provider:
        normalized["provider"] = provider
    return normalized


# ── 内部同步原子操作（在线程中运行）─────────────────────────────────────────

def _sync_upsert(data: dict) -> Key:
    """
    有 id → 更新对应记录；无 id → 插入新记录。
    返回操作后的 Key 对象。
    """
    data = _apply_provider_detection(data)
    with Session(engine) as session:
        key_id = data.get("id")
        if key_id:
            obj = session.get(Key, key_id)
            if obj is None:
                raise ValueError(f"Key id={key_id} 不存在")
            for field, value in data.items():
                if field != "id" and hasattr(obj, field):
                    setattr(obj, field, value)
        else:
            obj = Key(**{k: v for k, v in data.items() if hasattr(Key, k)})
            session.add(obj)

        session.add(obj)
        session.commit()
        session.refresh(obj)
        return obj


def _sync_delete(key_id: int) -> bool:
    with Session(engine) as session:
        obj = session.get(Key, key_id)
        if obj is None:
            return False
        session.delete(obj)
        session.commit()
        return True


def _sync_update(key_id: int, data: dict) -> Key:
    data = _apply_provider_detection(data)
    with Session(engine) as session:
        obj = session.get(Key, key_id)
        if obj is None:
            raise ValueError(f"Key id={key_id} 不存在")
        for field, value in data.items():
            if field != "id" and hasattr(obj, field):
                setattr(obj, field, value)
        session.add(obj)
        session.commit()
        session.refresh(obj)
        return obj


def _sync_get_all(provider: str | None = None) -> list[Key]:
    with Session(engine) as session:
        stmt = select(Key)
        if provider:
            stmt = stmt.where(Key.provider == provider)
        return session.exec(stmt).all()


def _sync_get_one(key_id: int) -> Key | None:
    with Session(engine) as session:
        return session.get(Key, key_id)


# ── 公开 async 接口 ──────────────────────────────────────────────────────────

async def upsert_keys(
    data: list[dict] | dict,
    concurrent: int = 20,
) -> list[Key]:
    """
    批量或单个 插入/更新。

    - data 中有 "id" → 更新
    - data 中无 "id" → 插入
    - 支持单个 dict 或 list[dict]
    """
    items = [data] if isinstance(data, dict) else data
    return await gather_limited(
        [asyncio.to_thread(_sync_upsert, d) for d in items],
        concurrent=concurrent,
    )


async def delete_keys(
    ids: list[int] | int,
    concurrent: int = 20,
) -> list[bool]:
    """
    批量或单个删除，返回每个 id 的操作结果（True=成功，False=不存在）。
    """
    id_list = [ids] if isinstance(ids, int) else ids
    return await gather_limited(
        [asyncio.to_thread(_sync_delete, i) for i in id_list],
        concurrent=concurrent,
    )


async def update_keys(
    updates: list[dict] | dict,
    concurrent: int = 20,
) -> list[Key]:
    """
    批量或单个字段更新，每项 dict 必须包含 "id"。

    示例：
        await update_keys({"id": 1, "tier": "3", "notes": "已验证"})
        await update_keys([{"id": 1, "tier": "3"}, {"id": 2, "notes": "x"}])
    """
    items = [updates] if isinstance(updates, dict) else updates

    async def _one(d: dict) -> Key:
        key_id = d.get("id")
        if key_id is None:
            raise ValueError("update_keys 的每项 dict 必须包含 'id'")
        payload = {k: v for k, v in d.items() if k != "id"}
        return await asyncio.to_thread(_sync_update, key_id, payload)

    return await gather_limited(
        [_one(d) for d in items],
        concurrent=concurrent,
    )


async def get_keys(provider: str | None = None) -> list[Key]:
    """查询所有 key，可按 provider 过滤。"""
    return await asyncio.to_thread(_sync_get_all, provider)


async def get_key(key_id: int) -> Key | None:
    """查询单个 key。"""
    return await asyncio.to_thread(_sync_get_one, key_id)
