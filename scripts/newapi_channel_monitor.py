#!/usr/bin/env python3
"""监控指定渠道的成功率，低于阈值则自动禁用该渠道。

成功率定义：最近 LIMIT 条日志中 type==2 的占比。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from loguru import logger

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.http_client import close_http_client, init_http_client
from app.services.newapi_service import NewAPIService

BASE_URL = "http://67.21.92.138:3011"
USERNAME = "root"
PASSWORD = "root666."

# ── 配置区 ────────────────────────────────────────────────
CHANNEL_IDS: list[int] = [7]   # 要监控的渠道 ID 列表
THRESHOLD: float = 0.3             # 成功率低于此值则禁用
LIMIT: int = 50                     # 每个渠道抽取最近 N 条日志
# ─────────────────────────────────────────────────────────


async def calc_success_rate(svc: NewAPIService, channel_id: int) -> tuple[float, int, int]:
    """返回 (成功率, ok 数, 总条数)。"""
    raw = await svc.get_logs(page=1, page_size=LIMIT, channel_id=channel_id)
    rows: list[dict] = []
    if isinstance(raw, dict):
        rows = raw.get("items") or raw.get("data") or []
    elif isinstance(raw, list):
        rows = raw
    rows = rows[:LIMIT]
    n = len(rows)
    ok = sum(1 for r in rows if isinstance(r, dict) and r.get("type") == 2)
    return (ok / n if n else 1.0), ok, n


async def main() -> None:
    await init_http_client()
    try:
        svc = NewAPIService(BASE_URL, USERNAME, PASSWORD)
        disabled: list[int] = []

        for cid in CHANNEL_IDS:
            try:
                ch = await svc.get_channel(cid)
            except Exception as exc:
                logger.warning("渠道 {} 获取信息失败：{}", cid, exc)
                continue

            name: str = ch.get("name", str(cid)) if isinstance(ch, dict) else str(cid)
            status: int = ch.get("status", 0) if isinstance(ch, dict) else 0
            if status != 1:
                logger.info("渠道 {}({}) 当前非启用状态（status={}），跳过", cid, name, status)
                continue

            try:
                rate, ok, n = await calc_success_rate(svc, cid)
            except Exception as exc:
                logger.warning("渠道 {}({}) 获取日志失败：{}", cid, name, exc)
                continue

            label = "  ← 低于阈值" if rate < THRESHOLD else ""
            logger.info("渠道 {}({}) 成功率 {:.0%}  ({}/{}){}", cid, name, rate, ok, n, label)

            if rate < THRESHOLD:
                try:
                    await svc.disable_channel(cid)
                    logger.warning("已禁用渠道 {}({})，成功率 {:.0%}", cid, name, rate)
                    disabled.append(cid)
                except Exception as exc:
                    logger.error("禁用渠道 {}({}) 失败：{}", cid, name, exc)

    finally:
        await close_http_client()

    if disabled:
        logger.info("本次共禁用 {} 个渠道：{}", len(disabled), disabled)
    else:
        logger.info("所有渠道成功率均正常，无需禁用")


if __name__ == "__main__":
    asyncio.run(main())
