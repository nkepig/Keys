#!/usr/bin/env python3
"""最近 20 条日志里 type==2 的占比；低于 80% 则按 data/newapi_monitor_state.json 冷却后发告警邮件。"""
from __future__ import annotations

import asyncio
import json
import smtplib
import sys
import time
from email.message import EmailMessage
from pathlib import Path

from loguru import logger

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import settings
from app.http_client import close_http_client, init_http_client
from app.services.newapi_service import NewAPIService

STATE_PATH = ROOT / "data" / "newapi_monitor_state.json"
base_url = "http://67.21.92.138:3011"
username = "root"
password = "root666."
LIMIT = 50
THRESHOLD = 0.6
COOLDOWN_SEC = 600


async def main() -> None:
    await init_http_client()
    try:
        raw = await NewAPIService(base_url, username, password).get_logs(username="viet")
        rows = raw["items"] if isinstance(raw, dict) and isinstance(raw.get("items"), list) else raw
        rows = rows[:LIMIT] if isinstance(rows, list) else []
        n = len(rows)
        ok = sum(1 for r in rows if isinstance(r, dict) and r.get("type") == 2)
        rate = ok / n if n else 1.0
        logger.info("最近{}条 type==2 占比 {:.0%} ({}/{})", n, rate, ok, n)
    finally:
        await close_http_client()

    if not n or rate >= THRESHOLD:
        raise SystemExit(0)

    now = time.time()
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    last = json.loads(STATE_PATH.read_text()).get("last_alert_ts", 0) if STATE_PATH.exists() else 0
    if now - last < COOLDOWN_SEC:
        logger.warning("冷却 {:.0f}s 内跳过发信", COOLDOWN_SEC - (now - last))
        raise SystemExit(1)

    msg = EmailMessage()
    msg["Subject"] = f"[告警] New API {base_url}"
    msg["From"] = settings.smtp_user
    msg["To"] = ", ".join(settings.email_recipients)
    msg.set_content(
        f"站点监控：{base_url}\n\n"
        f"最近五十条成功率：{rate:.0%}\n"
        f"阈值：{THRESHOLD:.0%}\n"
        f"成功率低于阈值，请及时处理。",
        charset="utf-8",
    )

    def send():
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as s:
            s.starttls()
            s.login(settings.smtp_user, settings.smtp_password)
            s.send_message(msg)

    await asyncio.to_thread(send)
    STATE_PATH.write_text(json.dumps({"last_alert_ts": now}))
    logger.info("告警邮件已发送")

if __name__ == "__main__":
    asyncio.run(main())
