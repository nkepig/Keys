"""
Key 服务

分层结构：
  db_*      纯数据库操作（同步），通过 asyncio.to_thread 在线程池中运行
  业务函数   组合 db_* + LLM 校验，对外暴露 async 接口
"""
import asyncio
import json
import re

from sqlmodel import Session, select

from app.db import engine
from app.models.key import Key
from app.services.llm.claude import ClaudeService
from app.services.llm.gemini import GeminiService
from app.services.llm.openai import OpenAIService
from app.services.llm.openrouter import OpenRouterService
from app.utils.concurrency import gather_limited

# ── 供应商识别规则 ────────────────────────────────────────────────────────────

REGEX_RULES: dict[str, list[str]] = {
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

# ── 供应商识别工具 ────────────────────────────────────────────────────────────

def detect_provider_and_key(raw_text: str | None) -> tuple[str | None, str | None]:
    """从原始文本中识别供应商并提取 key，未匹配返回 (None, None)。"""
    if not raw_text:
        return None, None
    for provider, pattern in _COMPILED_RULES:
        m = pattern.search(raw_text.strip())
        if m:
            return provider, m.group(0)
    return None, None


def detect_all_keys_from_text(text: str) -> list[tuple[str, str]]:
    """在任意文本中提取全部 (provider, key) 对，适用于页面扫描场景。"""
    seen: set[str] = set()
    results: list[tuple[str, str]] = []
    for provider, pattern in _COMPILED_RULES:
        for m in pattern.finditer(text):
            key = m.group(0)
            if key not in seen:
                seen.add(key)
                results.append((provider, key))
    return results


def _normalize_key_data(data: dict) -> dict:
    """若能识别到 key，写回标准化 key 并覆盖 provider，否则保留原值。"""
    normalized = dict(data)
    provider, detected_key = detect_provider_and_key(normalized.get("key"))
    if detected_key:
        normalized["key"] = detected_key
    if provider:
        normalized["provider"] = provider
    return normalized


# ── DB 操作层（同步，通过 asyncio.to_thread 调用）─────────────────────────────

def db_insert_key(
    provider: str,
    key: str,
    origin: str | None,
    tier: str | None,
    models_json: str | None,
    status_code: int | None,
) -> Key:
    with Session(engine) as session:
        obj = Key(provider=provider, key=key, origin=origin,
                  tier=tier, models=models_json, status_code=status_code)
        session.add(obj)
        session.commit()
        session.refresh(obj)
        return obj


def db_get_existing_keys(keys: list[str]) -> frozenset[str]:
    """一次 IN 查询，返回已存在的 key 集合。"""
    if not keys:
        return frozenset()
    with Session(engine) as session:
        return frozenset(session.exec(select(Key.key).where(Key.key.in_(keys))).all())


def db_get_key(key_id: int) -> Key | None:
    with Session(engine) as session:
        return session.get(Key, key_id)


def db_get_all_keys(provider: str | None = None) -> list[Key]:
    with Session(engine) as session:
        stmt = select(Key)
        if provider:
            stmt = stmt.where(Key.provider == provider)
        return session.exec(stmt).all()


def db_upsert_key(data: dict) -> Key:
    """有 id → 更新；无 id → 插入。"""
    data = _normalize_key_data(data)
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


def db_update_key(key_id: int, data: dict) -> Key:
    data = _normalize_key_data(data)
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


def db_delete_key(key_id: int) -> bool:
    with Session(engine) as session:
        obj = session.get(Key, key_id)
        if obj is None:
            return False
        session.delete(obj)
        session.commit()
        return True


# ── LLM 校验层 ───────────────────────────────────────────────────────────────

_VERIFY_SERVICES = {
    "OpenAI":     OpenAIService,
    "Anthropic":  ClaudeService,
    "Google":     GeminiService,
    "OpenRouter": OpenRouterService,
}

_MODEL_FETCHERS = {
    "OpenAI":    OpenAIService.fetch_models,
    "Anthropic": ClaudeService.fetch_models,
    "Google":    GeminiService.fetch_models,
}


async def verify_key(provider: str, key: str) -> dict:
    """调用对应供应商的校验服务，返回 {status_code, tier}。"""
    service = _VERIFY_SERVICES.get(provider)
    if service is None:
        return {"status_code": None, "tier": None}
    try:
        return await service.verify(key)
    except Exception:
        return {"status_code": None, "tier": None}


async def fetch_models_for(provider: str, key: str) -> list[str]:
    """status_code=200 时获取支持的模型列表。"""
    fetcher = _MODEL_FETCHERS.get(provider)
    if fetcher is None:
        return []
    try:
        return await fetcher(key)
    except Exception:
        return []


# ── 业务层 ───────────────────────────────────────────────────────────────────

def _mask_key(key: str) -> str:
    return f"{key[:6]}•••{key[-4:]}" if len(key) > 10 else "••••••••"


async def process_key(raw_key: str, origin: str | None = None) -> dict:
    """识别供应商 → 校验 → 保存。重复检查由上层 batch_process_keys 负责。"""
    provider, clean_key = detect_provider_and_key(raw_key)
    if not clean_key or not provider:
        return {
            "id": None, "key": None, "provider": None,
            "status_code": None, "tier": None, "models_count": 0,
            "saved": False, "error": "无法识别供应商或 key",
        }

    result = await verify_key(provider, clean_key)
    status_code = result["status_code"]
    tier_val = str(result["tier"]) if result["tier"] is not None else None

    models_json: str | None = None
    if status_code == 200:
        model_list = await fetch_models_for(provider, clean_key)
        if model_list:
            models_json = json.dumps(model_list, ensure_ascii=False)

    saved = await asyncio.to_thread(
        db_insert_key, provider, clean_key, origin, tier_val, models_json, status_code
    )
    return {
        "id": saved.id, "key": _mask_key(clean_key), "provider": provider,
        "status_code": status_code, "tier": tier_val,
        "models_count": len(json.loads(models_json)) if models_json else 0,
        "saved": True, "error": None,
    }


async def batch_process_keys(
    items: list[dict] | str,
    origin: str | None = None,
    concurrent: int = 10,
) -> list[dict]:
    """
    统一批量入口：去重 → DB 过滤已存在 → 并发校验保存。

    items 可以是：
      - str：原始文本，一行一个 key，统一使用 origin 参数
      - list[dict]：每项含 "key"（必须）和可选 "origin"，支持每条独立来源
    """
    if isinstance(items, str):
        rows = [
            {"key": line, "origin": origin}
            for line in dict.fromkeys(l.strip() for l in items.splitlines() if l.strip())
        ]
    else:
        seen: set[str] = set()
        rows = []
        for item in items:
            k = item.get("key", "").strip()
            if k and k not in seen:
                seen.add(k)
                rows.append({"key": k, "origin": item.get("origin", origin)})

    if not rows:
        return []

    candidate_keys = [ck for _, ck in (detect_provider_and_key(r["key"]) for r in rows) if ck]
    existing = await asyncio.to_thread(db_get_existing_keys, candidate_keys)

    skip_results, tasks = [], []
    for row in rows:
        _, clean_key = detect_provider_and_key(row["key"])
        if clean_key and clean_key in existing:
            skip_results.append({
                "id": None, "key": _mask_key(clean_key), "provider": None,
                "status_code": None, "tier": None, "models_count": 0,
                "saved": False, "error": "已存在，跳过",
            })
        else:
            tasks.append(process_key(row["key"], row["origin"]))

    verify_results = await gather_limited(tasks, concurrent=concurrent) if tasks else []
    return skip_results + verify_results


# ── 公开 CRUD 接口 ────────────────────────────────────────────────────────────

async def get_keys(provider: str | None = None) -> list[Key]:
    return await asyncio.to_thread(db_get_all_keys, provider)


async def get_key(key_id: int) -> Key | None:
    return await asyncio.to_thread(db_get_key, key_id)


async def upsert_keys(data: list[dict] | dict, concurrent: int = 20) -> list[Key]:
    items = [data] if isinstance(data, dict) else data
    return await gather_limited(
        [asyncio.to_thread(db_upsert_key, d) for d in items],
        concurrent=concurrent,
    )


async def update_keys(updates: list[dict] | dict, concurrent: int = 20) -> list[Key]:
    items = [updates] if isinstance(updates, dict) else updates

    async def _one(d: dict) -> Key:
        key_id = d.get("id")
        if key_id is None:
            raise ValueError("update_keys 的每项 dict 必须包含 'id'")
        return await asyncio.to_thread(db_update_key, key_id, {k: v for k, v in d.items() if k != "id"})

    return await gather_limited([_one(d) for d in items], concurrent=concurrent)


async def delete_keys(ids: list[int] | int, concurrent: int = 20) -> list[bool]:
    id_list = [ids] if isinstance(ids, int) else ids
    return await gather_limited(
        [asyncio.to_thread(db_delete_key, i) for i in id_list],
        concurrent=concurrent,
    )
