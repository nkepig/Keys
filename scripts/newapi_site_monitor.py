#!/usr/bin/env python3
"""最近若干条日志里 type==2 的占比；成功率 < THRESHOLD 时告警。

邮件与 fwalert 共用冷却；fwalert 仅在北京时间 00:00–09:00（不含 9 点整）触发。
"""
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
from app.http_client import close_http_client, get_http_session, init_http_client
from app.services.newapi_service import NewAPIService
from app.utils.timezone import china_now

STATE_PATH = ROOT / "data" / "newapi_monitor_state.json"
BASE_URL = "http://67.21.92.138:3011"
username = "root"
password = "root666."
LIMIT = 50
THRESHOLD = 0.3
COOLDOWN_SEC = 600
FWALERT_URL = "https://fwalert.com/0fe4f0f5-a969-4599-8e72-78582942bc08"
FWALERT_BJ_END_HOUR = 9  # 北京时间 hour < 此值时请求 fwalert（00:00–08:59）
ALERT_SUBJECT = f"[告警] New API {BASE_URL}"
ALERT_BODY_FMT = "站点监控：{base_url}\n\n最近 {limit} 条成功率：{rate:.0%}\n阈值：{threshold:.0%}\n成功率低于阈值，请及时处理。"


async def trigger_fwalert() -> None:
    """GET fwalert（与邮件同冷却，由调用方保证）。"""
    await init_http_client()
    try:
        session = get_http_session()
        async with session.get(FWALERT_URL) as resp:
            logger.info("fwalert HTTP {}", resp.status)
            if resp.status >= 400:
                logger.warning("fwalert 非成功状态 {}", resp.status)
    except Exception:
        logger.exception("fwalert 请求失败")
    finally:
        await close_http_client()


async def main() -> None:
    await init_http_client()
    try:
        raw = await NewAPIService(BASE_URL, username, password).get_logs(username="viet")
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

    body = ALERT_BODY_FMT.format(base_url=BASE_URL, limit=LIMIT, rate=rate, threshold=THRESHOLD)

    if now - last >= COOLDOWN_SEC:
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = (
            settings.smtp_user,
            ", ".join(settings.email_recipients),
            ALERT_SUBJECT,
        )
        msg.set_content(body, charset="utf-8")

        def send() -> None:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as s:
                s.starttls()
                s.login(settings.smtp_user, settings.smtp_password)
                s.send_message(msg)

        try:
            await asyncio.to_thread(send)
            logger.info("告警邮件已发送")
        except Exception:
            logger.exception("邮件发送失败（fwalert 仅北京 0–{} 点）", FWALERT_BJ_END_HOUR)
        STATE_PATH.write_text(json.dumps({"last_alert_ts": now}))

        if china_now().hour < FWALERT_BJ_END_HOUR:
            await trigger_fwalert()
        else:
            logger.info("北京时间非 0–{} 点，跳过 fwalert", FWALERT_BJ_END_HOUR)
    else:
        logger.warning("冷却 {:.0f}s 内跳过邮件与 fwalert", COOLDOWN_SEC - (now - last))


if __name__ == "__main__":
    asyncio.run(main())
