#!/usr/bin/env python3
"""
批量重新校验非 401 状态的密钥，并输出状态变更摘要。

用法:
    python scripts/verify_keys.py
"""
import asyncio
import json
import sys
from pathlib import Path

from loguru import logger

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from app.http_client import close_http_client
from app.models.key import Key
from app.services import key_service
from app.services.key_service import _verify_dispatch, fetch_models_for
from app.utils.concurrency import gather_limited


def _mask(key: str) -> str:
    if not key or len(key) <= 10:
        return key
    return f"{key[:3]}...{key[-7:]}"


async def _reverify_one(k: Key) -> tuple[int, int | None, int | None]:
    """校验单个 Key，更新 DB，返回 (id, old_code, new_code)。"""
    old_code = k.status_code
    result = await _verify_dispatch(k.provider, k.key)
    new_code = result["status_code"]
    tier = str(result["tier"]) if result["tier"] is not None else None

    update: dict = {"id": k.id, "status_code": new_code}
    if tier is not None:
        update["tier"] = tier
    if new_code == 200:
        models = await fetch_models_for(k.provider, k.key)
        update["models"] = json.dumps(models, ensure_ascii=False) if models else None
    else:
        update["models"] = None

    await key_service.update_keys(update)
    return k.id, old_code, new_code


async def reverify_all(concurrent: int = 20) -> None:
    all_keys = await key_service.get_keys()
    targets = [k for k in all_keys if k.status_code != 401]

    logger.info(f"共 {len(all_keys)} 条密钥，本次校验 {len(targets)} 条（排除 401）")
    if not targets:
        logger.warning("没有需要校验的密钥")
        return

    results = await gather_limited(
        [_reverify_one(k) for k in targets],
        concurrent=concurrent,
    )

    changes = [(kid, old, new) for kid, old, new in results if old != new]

    logger.info(f"\n{'='*55}")
    if changes:
        # 按供应商分组展示
        id_to_key = {k.id: k for k in targets}
        by_provider: dict[str, list] = {}
        for kid, old, new in sorted(changes):
            p = id_to_key[kid].provider
            by_provider.setdefault(p, []).append((kid, id_to_key[kid].key, old, new))

        logger.info(f"共 {len(changes)} 条密钥状态发生变更:")
        for provider, items in sorted(by_provider.items()):
            logger.info(f"\n  {provider} ({len(items)} 条):")
            for kid, raw_key, old, new in items:
                logger.info(f"    id={kid}  {_mask(raw_key)}  {old} → {new}")
    else:
        logger.info("本次校验无状态变更")

    logger.info(f"{'='*55}")
    logger.success(f"校验完成：处理 {len(targets)} 条，变更 {len(changes)} 条")


async def main():
    try:
        await reverify_all(concurrent=20)
    finally:
        await close_http_client()
        logger.info("资源清理完成")


if __name__ == "__main__":
    asyncio.run(main())
