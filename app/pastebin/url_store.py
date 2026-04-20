"""Pastebin 已见 URL 的本地 SQLite（与主库 keys.db 分离）。"""
from __future__ import annotations

import asyncio
import os
import sqlite3


def ensure_parent_dir(path: str) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)


def init_urls_table_sqlite_sync(db_path: str) -> None:
    ensure_parent_dir(db_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS urls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL UNIQUE,
                source TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_urls_url ON urls(url)")
        conn.commit()
    finally:
        conn.close()


async def init_urls_table_sqlite(db_path: str) -> None:
    await asyncio.to_thread(init_urls_table_sqlite_sync, db_path)


async def load_existing_urls_sqlite(db_path: str) -> set[str]:
    def _load() -> set[str]:
        if not os.path.isfile(db_path):
            return set()
        conn = sqlite3.connect(db_path)
        try:
            rows = conn.execute("SELECT url FROM urls").fetchall()
            return {r[0] for r in rows if r[0]}
        finally:
            conn.close()

    return await asyncio.to_thread(_load)


async def filter_new_urls_local(urls: list[str], existing_urls: set[str]) -> list[str]:
    new_urls: list[str] = []
    for url in urls:
        u = url.strip()
        if u and u not in existing_urls:
            new_urls.append(u)
    return new_urls


async def batch_save_urls_sqlite(db_path: str, urls: list[str], source: str) -> dict:
    def _save() -> dict:
        clean_urls = list({u.strip() for u in urls if u and u.strip()})
        if not clean_urls:
            return {"success": 0, "duplicate": len(urls), "error": 0, "total": len(urls)}
        ensure_parent_dir(db_path)
        conn = sqlite3.connect(db_path)
        try:
            before = conn.execute("SELECT COUNT(*) FROM urls").fetchone()[0]
            conn.executemany(
                "INSERT OR IGNORE INTO urls (url, source) VALUES (?, ?)",
                [(u, source) for u in clean_urls],
            )
            conn.commit()
            after = conn.execute("SELECT COUNT(*) FROM urls").fetchone()[0]
            inserted = after - before
            return {
                "success": inserted,
                "duplicate": len(clean_urls) - inserted,
                "error": 0,
                "total": len(urls),
            }
        finally:
            conn.close()

    return await asyncio.to_thread(_save)
