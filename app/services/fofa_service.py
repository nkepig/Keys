import base64
import random

from app.config import settings
from app.http_client import get_http_session

_BASE_URL = "https://en.fofa.info/api/v1/search/all"


async def fofa_search(query: str, fields: str = "host", size: int = 10000) -> list[str]:
    """查询 FOFA，返回去重排序后的主机列表。每次随机选取一个 API Key。"""
    params = {
        "key": random.choice(settings.fofa_api_keys),
        "qbase64": base64.b64encode(query.encode()).decode(),
        "fields": fields,
        "size": size,
    }
    async with get_http_session().get(_BASE_URL, params=params) as resp:
        resp.raise_for_status()
        data = await resp.json()

    if data.get("error"):
        raise RuntimeError(f"FOFA 错误：{data.get('errmsg')}")

    hosts = set()
    for row in data.get("results", []):
        raw = row if isinstance(row, str) else (str(row[0]) if isinstance(row, list) and row else "")
        if raw:
            hosts.add(raw.strip())
    return sorted(hosts)
