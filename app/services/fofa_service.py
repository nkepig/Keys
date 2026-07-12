import base64
import json
import random

import aiohttp
from loguru import logger

from app.config import settings
from app.http_client import get_http_session

_BASE_URL = "https://fofoapi.com/api/v1/search/all"


def _extract_api_error(data: dict) -> str | None:
    if data.get("error") is True:
        return str(data.get("errmsg") or data.get("message") or data)
    errmsg = data.get("errmsg") or data.get("message")
    if errmsg and str(errmsg).lower() not in ("", "success", "ok"):
        return str(errmsg)
    return None


async def fofa_search(query: str, fields: str = "host", size: int = 10000) -> list[str]:
    """查询 FOFA，返回去重排序后的主机列表。每次随机选取一个 API Key。"""
    if not settings.fofa_api_keys:
        logger.error("FOFA API Key 未配置（settings.fofa_api_keys 为空）")
        return []

    params = {
        "key": random.choice(settings.fofa_api_keys),
        "qbase64": base64.b64encode(query.encode()).decode(),
        "fields": fields,
        "size": size,
    }
    try:
        async with get_http_session().get(_BASE_URL, params=params) as resp:
            body_text = await resp.text()
            if resp.status >= 400:
                logger.error(
                    "FOFA HTTP {}: query={!r} body={}",
                    resp.status,
                    query,
                    body_text[:500],
                )
                return []

            try:
                data = json.loads(body_text)
            except json.JSONDecodeError:
                logger.error(
                    "FOFA 响应非 JSON: status={} query={!r} body={}",
                    resp.status,
                    query,
                    body_text[:500],
                )
                return []

            if not isinstance(data, dict):
                logger.error("FOFA 响应格式异常: query={!r} type={}", query, type(data).__name__)
                return []

            api_error = _extract_api_error(data)
            if api_error:
                logger.error("FOFA API 错误: query={!r} errmsg={}", query, api_error)
                return []

            results = data.get("results")
            if results is None:
                logger.warning("FOFA 响应缺少 results 字段: query={!r} keys={}", query, list(data.keys()))
                return []

    except aiohttp.ClientResponseError as e:
        logger.error(
            "FOFA HTTP 错误: status={} query={!r} message={}",
            e.status,
            query,
            e.message,
        )
        return []
    except Exception as e:
        logger.exception("FOFA 请求失败: query={!r} error={}", query, e)
        return []

    hosts = set()
    for row in results:
        raw = row if isinstance(row, str) else (str(row[0]) if isinstance(row, list) and row else "")
        if raw:
            hosts.add(raw.strip())
    return sorted(hosts)
