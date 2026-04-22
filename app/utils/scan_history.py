from __future__ import annotations

from collections.abc import Iterable
from sqlalchemy import text
from urllib.parse import urlparse

from app.db import engine

SCAN_HISTORY_WINDOW_DAYS = 7
SCAN_HISTORY_MATCH_TARGET = "target"
SCAN_HISTORY_MATCH_NETLOC = "netloc"


def normalize_netloc(value: str | None) -> str | None:
    if not value or not str(value).strip():
        return None
    target = str(value).strip()
    if "://" not in target:
        target = "http://" + target.split("/")[0]
    try:
        netloc = urlparse(target).netloc.lower()
        return netloc or None
    except Exception:
        return None


def _normalize_target(value: str | None, match_type: str) -> str | None:
    if match_type == SCAN_HISTORY_MATCH_NETLOC:
        return normalize_netloc(value)
    if not value or not str(value).strip():
        return None
    return str(value).strip()


def load_recent_scan_history_targets(
    source: str,
    match_type: str = SCAN_HISTORY_MATCH_TARGET,
    window_days: int = SCAN_HISTORY_WINDOW_DAYS,
) -> set[str]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT target
                FROM scan_history
                WHERE source = :source
                  AND match_type = :match_type
                  AND scanned_at >= datetime('now', :window)
                """
            ),
            {"source": source, "match_type": match_type, "window": f"-{window_days} days"},
        ).fetchall()
    return {row[0] for row in rows if row[0]}


def prune_scan_history(window_days: int = SCAN_HISTORY_WINDOW_DAYS) -> int:
    with engine.begin() as conn:
        result = conn.execute(
            text(
                """
                DELETE FROM scan_history
                WHERE scanned_at < datetime('now', :window)
                """
            ),
            {"window": f"-{window_days} days"},
        )
    return max(result.rowcount or 0, 0)


def save_scan_history(
    targets: Iterable[str],
    source: str,
    match_type: str = SCAN_HISTORY_MATCH_TARGET,
) -> int:
    rows: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for target in targets:
        raw = str(target).strip()
        normalized_target = _normalize_target(raw, match_type)
        netloc = normalize_netloc(raw)
        key = (match_type, normalized_target or "")
        if not raw or not normalized_target or key in seen:
            continue
        seen.add(key)
        rows.append(
            {
                "source": source,
                "target": normalized_target,
                "netloc": netloc or normalized_target,
                "match_type": match_type,
            }
        )

    if not rows:
        return 0

    with engine.begin() as conn:
        for row in rows:
            conn.execute(
                text(
                    """
                    INSERT OR REPLACE INTO scan_history (source, target, netloc, match_type, scanned_at)
                    VALUES (:source, :target, :netloc, :match_type, datetime('now'))
                    """
                ),
                row,
            )
    return len(rows)
