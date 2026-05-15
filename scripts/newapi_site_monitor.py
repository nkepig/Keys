#!/usr/bin/env python3
"""最近若干条日志里 type==2 的占比；成功率 < THRESHOLD 时告警。

邮件有冷却；Bark 无冷却，且仅在北京时间 00:00–09:00（不含 9 点整）推送。
"""
from __future__ import annotations

import asyncio
import json
import smtplib
import sys
import time
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import quote

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
BARK_KEY = "w92Rkx7wTKHGuy9SU5Qtga"
BARK_BJ_END_HOUR = 9  # 北京时间 hour < 此值时发 Bark（即 00:00–08:59 段，到 9 点前）
ALERT_SUBJECT = f"[告警] New API {BASE_URL}"
ALERT_BODY_FMT = "站点监控：{base_url}\n\n最近 {limit} 条成功率：{rate:.0%}\n阈值：{threshold:.0%}\n成功率低于阈值，请及时处理。"
BARK_URL_TMPL = f"https://bark.dazes.cc/{BARK_KEY}/{{enc_subj}}/{{enc_body}}?level=critical&sound=alarm&volume=10"
BARK_BURST_SLEEP_SEC = 3.0  # 单次 Bark 铃声约 3s，间隔过短会叠在一起


async def bark_burst_10s(url: str) -> None:
    """约 15 秒内重复请求 Bark（无冷却，由调用方在失败时调用）。"""
    logger.warning("成功率低于阈值，Bark 约 15 秒")
    await init_http_client()
    try:
        session = get_http_session()
        until = time.monotonic() + 15.0
        while time.monotonic() < until:
            try:
                async with session.get(url) as resp:
                    logger.info("Bark HTTP {}", resp.status)
            except Exception:
                logger.exception("Bark 请求失败")
            remaining = until - time.monotonic()
            if remaining <= 0:
                break
            await asyncio.sleep(min(BARK_BURST_SLEEP_SEC, remaining))
    finally:
        await close_http_client()
    logger.info("Bark 已发送")


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
    bark_url = BARK_URL_TMPL.format(enc_subj=quote(ALERT_SUBJECT, safe=""), enc_body=quote(body, safe=""))

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
            logger.exception("邮件发送失败（Bark 仅北京 0–{} 点）", BARK_BJ_END_HOUR)
        STATE_PATH.write_text(json.dumps({"last_alert_ts": now}))
    else:
        logger.warning("冷却 {:.0f}s 内跳过邮件", COOLDOWN_SEC - (now - last))

    if china_now().hour < BARK_BJ_END_HOUR:
        await bark_burst_10s(bark_url)
    else:
        logger.info("北京时间非 0–{} 点，跳过 Bark", BARK_BJ_END_HOUR)


if __name__ == "__main__":
    asyncio.run(main())
