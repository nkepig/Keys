import aiohttp

_session: aiohttp.ClientSession | None = None
_TIMEOUT = aiohttp.ClientTimeout(total=120)


async def init_http_client() -> None:
    global _session
    _session = aiohttp.ClientSession(timeout=_TIMEOUT)


async def close_http_client() -> None:
    global _session
    if _session:
        await _session.close()
        _session = None


def get_http_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession(timeout=_TIMEOUT)
    return _session
