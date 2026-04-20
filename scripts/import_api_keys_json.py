#!/usr/bin/env python3
"""将 api_keys_non401.json 等导出文件导入本地 SQLite（不重复调用远程校验）。

用法:
    cd /root/Keys && source .venv/bin/activate && python scripts/import_api_keys_json.py
    python scripts/import_api_keys_json.py path/to/export.json
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from sqlmodel import Session, select

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from app.db import engine, init_db  # noqa: E402
from app.models.key import Key  # noqa: E402
from app.services.key_service import db_get_existing_keys, detect_provider_and_key  # noqa: E402


def _parse_status_code(state: object) -> int | None:
    if state is None:
        return None
    if isinstance(state, int):
        return state
    s = str(state).strip()
    if s.isdigit():
        return int(s)
    return None


def _models_json(raw: object) -> str | None:
    if raw is None:
        return None
    if isinstance(raw, str):
        return raw if raw.strip() else None
    return json.dumps(raw, ensure_ascii=False)


def _tier_str(raw: object) -> str | None:
    if raw is None:
        return None
    return str(raw)


def _parse_created_at(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _row_to_key_fields(item: dict) -> dict | None:
    raw_key = (item.get("key") or "").strip()
    if not raw_key:
        return None
    provider, clean_key = detect_provider_and_key(raw_key)
    if not clean_key or not provider:
        provider = (item.get("provider") or "").strip() or None
        clean_key = raw_key
        if not provider:
            return None

    origin = item.get("source")
    if isinstance(origin, str):
        origin = origin.strip() or None
    else:
        origin = str(origin) if origin is not None else None

    ct = _parse_created_at(item.get("created_at"))
    remark = item.get("remark")
    notes = str(remark).strip() if remark is not None and str(remark).strip() else None

    return {
        "provider": provider,
        "key": clean_key,
        "origin": origin,
        "tier": _tier_str(item.get("grade")),
        "models": _models_json(item.get("models")),
        "status_code": _parse_status_code(item.get("state")),
        "notes": notes,
        "create_time": ct,
    }


def main() -> int:
    p = argparse.ArgumentParser(description="从 JSON 导出导入 keys 表")
    p.add_argument(
        "json_path",
        nargs="?",
        default=str(project_root / "api_keys_non401.json"),
        help="导出文件路径，默认项目根目录下 api_keys_non401.json",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="只统计将插入/跳过数量，不写库",
    )
    args = p.parse_args()
    path = Path(args.json_path)
    if not path.is_file():
        print(f"文件不存在: {path}", file=sys.stderr)
        return 1

    with path.open("r", encoding="utf-8") as f:
        payload = json.load(f)
    rows = payload.get("keys") or payload
    if not isinstance(rows, list):
        print("JSON 格式错误：需要 { \"keys\": [ ... ] } 或顶层为数组", file=sys.stderr)
        return 1

    init_db()

    parsed: list[dict] = []
    seen_keys: set[str] = set()
    dropped = 0
    dup_in_file = 0
    for item in rows:
        if not isinstance(item, dict):
            continue
        fields = _row_to_key_fields(item)
        if not fields:
            dropped += 1
            continue
        k = fields["key"]
        if k in seen_keys:
            dup_in_file += 1
            continue
        seen_keys.add(k)
        parsed.append(fields)

    if not parsed:
        print("没有可导入的记录")
        return 0

    existing = db_get_existing_keys([r["key"] for r in parsed])
    to_insert = [r for r in parsed if r["key"] not in existing]

    print(
        f"文件中 {len(rows)} 条 → 解析有效 {len(parsed)} 条 "
        f"(丢弃 {dropped}, 文件内重复 {dup_in_file}) | 库中已存在 {len(existing & seen_keys)} | 将插入 {len(to_insert)}"
    )

    if args.dry_run or not to_insert:
        return 0

    with Session(engine) as session:
        for r in to_insert:
            kw = dict(r)
            ct = kw.pop("create_time", None)
            obj = Key(**kw)
            if ct is not None:
                obj.create_time = ct
            session.add(obj)
        session.commit()

    print(f"已写入 {len(to_insert)} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
