#!/usr/bin/env python3
"""
将本地 keys.db 中的全部 API Key 导出到「远程」另一套 Keys 管理器（兼容本项目的 /api/keys/upload）。

流程：本地库读取 → 按 key 字符串去重 → 登录远程（与远程 .env 中 LOGIN_PASSWORD 一致）→ 分批 POST JSON。

用法（项目根目录、已激活 venv）:
    python scripts/export_keys_to_remote.py

在下面修改 REMOTE_BASE_URL / REMOTE_PASSWORD；也可用环境变量覆盖（见脚本内说明）。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

import aiohttp
from loguru import logger
from sqlmodel import Session, select

from app.db import engine
from app.models.key import Key

# ── 配置（优先填这里；留空则用环境变量 REMOTE_MANAGER_URL / REMOTE_MANAGER_PASSWORD）──
REMOTE_BASE_URL = "https://key.dazes.cc"  # 例: "https://your-remote-keys.example.com"
REMOTE_PASSWORD = "hui-daze"  # 与远程 .env 中 LOGIN_PASSWORD 一致
# 写入远程记录时的来源标记
ORIGIN_TAG = os.environ.get("REMOTE_EXPORT_ORIGIN", "本地导出同步")
# 每批上传行数（过大可能超时或超限）
BATCH_SIZE = 500
# 对应 /api/keys/upload 的 concurrent
UPLOAD_CONCURRENT = 100


def _load_unique_keys_ordered() -> list[str]:
    """从本地库取出全部 key，按出现顺序去重（同一字符串只保留第一次）。"""
    with Session(engine) as session:
        rows = session.exec(select(Key.key)).all()
    seen: set[str] = set()
    out: list[str] = []
    for raw in rows:
        if raw is None:
            continue
        s = str(raw).strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


async def _login(session: aiohttp.ClientSession, base: str, password: str) -> bool:
    url = f"{base}/login"
    data = aiohttp.FormData()
    data.add_field("password", password)
    data.add_field("next", "/keys")
    async with session.post(url, data=data, allow_redirects=True) as resp:
        if resp.status in (200, 303, 302):
            return True
        text = await resp.text()
        logger.error("登录失败 HTTP {}: {}", resp.status, text[:300])
        return False


async def _upload_batch(
    session: aiohttp.ClientSession,
    base: str,
    lines: list[str],
) -> dict | None:
    url = f"{base}/api/keys/upload"
    payload = {
        "keys": "\n".join(lines),
        "origin": ORIGIN_TAG,
        "concurrent": UPLOAD_CONCURRENT,
    }
    async with session.post(url, json=payload) as resp:
        text = await resp.text()
        if resp.status == 401:
            logger.error("远程返回 401 未登录，请检查 REMOTE_MANAGER_PASSWORD")
            return None
        if resp.status != 200:
            logger.error("上传失败 HTTP {}: {}", resp.status, text[:500])
            return None
        try:
            return json.loads(text)
        except Exception:
            logger.error("解析响应失败: {}", text[:300])
            return None


async def main() -> None:
    base = (REMOTE_BASE_URL or os.environ.get("REMOTE_MANAGER_URL", "")).strip().rstrip("/")
    pwd = (REMOTE_PASSWORD or os.environ.get("REMOTE_MANAGER_PASSWORD", "")).strip()
    if not base:
        logger.error("请设置 REMOTE_BASE_URL 或环境变量 REMOTE_MANAGER_URL")
        sys.exit(1)
    if not pwd:
        logger.error("请设置 REMOTE_PASSWORD 或环境变量 REMOTE_MANAGER_PASSWORD")
        sys.exit(1)

    unique = _load_unique_keys_ordered()
    logger.info("本地共 {} 条唯一 key（已去重）", len(unique))
    if not unique:
        logger.warning("没有可导出的 key")
        return

    timeout = aiohttp.ClientTimeout(total=600)
    jar = aiohttp.CookieJar(unsafe=True)
    async with aiohttp.ClientSession(timeout=timeout, cookie_jar=jar) as session:
        logger.info("正在登录远程: {}/login", base)
        if not await _login(session, base, pwd):
            sys.exit(1)
        logger.info("登录成功，开始分批上传（每批最多 {} 条）…", BATCH_SIZE)

        total_saved = 0
        total_failed = 0
        offset = 0
        batch_idx = 0
        while offset < len(unique):
            batch_idx += 1
            chunk = unique[offset : offset + BATCH_SIZE]
            offset += len(chunk)
            logger.info("上传第 {} 批，{} 条…", batch_idx, len(chunk))
            data = await _upload_batch(session, base, chunk)
            if data is None:
                sys.exit(1)
            total_saved += int(data.get("saved", 0))
            total_failed += int(data.get("failed", 0))
            logger.info(
                "第 {} 批完成: saved={}, failed={}, total响应={}",
                batch_idx,
                data.get("saved"),
                data.get("failed"),
                data.get("total"),
            )

        logger.success(
            "全部完成：远程累计 saved≈{} failed≈{}（远程会对已存在 key 跳过校验保存）",
            total_saved,
            total_failed,
        )


if __name__ == "__main__":
    asyncio.run(main())
