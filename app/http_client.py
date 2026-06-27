import aiohttp

_session: aiohttp.ClientSession | None = None
_connector: aiohttp.TCPConnector | None = None
_TIMEOUT = aiohttp.ClientTimeout(total=120)
# aiohttp 默认 limit=100，高并发扫描时会成为隐形瓶颈
_CONNECTOR_LIMIT = 500


def _get_connector() -> aiohttp.TCPConnector:
    global _connector
    if _connector is None or _connector.closed:
        _connector = aiohttp.TCPConnector(limit=_CONNECTOR_LIMIT, ttl_dns_cache=600)
    return _connector


def _make_session() -> aiohttp.ClientSession:
    return aiohttp.ClientSession(timeout=_TIMEOUT, connector=_get_connector())


async def init_http_client() -> None:
    global _session
    _session = _make_session()


async def close_http_client() -> None:
    global _session, _connector
    if _session:
        await _session.close()
        _session = None
    if _connector:
        await _connector.close()
        _connector = None


def get_http_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        _session = _make_session()
    return _session
