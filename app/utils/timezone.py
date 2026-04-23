from datetime import datetime, timedelta


def china_now() -> datetime:
    """Return current time in China (UTC+8) without timezone info.

    We keep naive datetime objects to align with existing database/storage logic
    in this project, which stores timestamps as TEXT without tzinfo.
    """
    return datetime.utcnow() + timedelta(hours=8)
