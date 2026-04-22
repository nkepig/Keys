from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping


def format_status_code_counts(counts: Mapping[object, int] | Counter) -> str:
    normalized: Counter[str] = Counter()
    for code, count in counts.items():
        if not count:
            continue
        label = "None" if code is None else str(code)
        normalized[label] += int(count)

    if not normalized:
        return "无状态码记录"

    def _sort_key(item: tuple[str, int]) -> tuple[int, str]:
        code, _ = item
        return (0, f"{int(code):06d}") if code.isdigit() else (1, code)

    return " | ".join(f"{code}={count}" for code, count in sorted(normalized.items(), key=_sort_key))


def count_status_codes(items: Iterable[Mapping[str, object]], field: str = "status_code") -> Counter:
    counter: Counter[object] = Counter()
    for item in items:
        counter[item.get(field)] += 1
    return counter
