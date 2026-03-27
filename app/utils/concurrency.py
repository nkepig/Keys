"""
并发工具 —— 信号量控制的 gather

用法：
    from app.utils.concurrency import gather_limited

    results = await gather_limited(
        [some_coro(i) for i in items],
        concurrent=10,
    )
"""
import asyncio
from collections.abc import Awaitable
from typing import TypeVar

T = TypeVar("T")


async def gather_limited(
    coros: list[Awaitable[T]],
    concurrent: int = 20,
    return_exceptions: bool = False,
) -> list[T]:
    """
    用信号量限制并发数地运行一批协程，返回与输入顺序对应的结果列表。

    参数：
        coros             协程列表（已创建的 coroutine 对象）
        concurrent        最大并发数，默认 20
        return_exceptions 若为 True，异常作为结果返回而非抛出
    """
    semaphore = asyncio.Semaphore(concurrent)

    async def _wrap(coro: Awaitable[T]) -> T:
        async with semaphore:
            return await coro

    return list(
        await asyncio.gather(
            *(_wrap(c) for c in coros),
            return_exceptions=return_exceptions,
        )
    )
