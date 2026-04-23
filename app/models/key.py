from datetime import datetime
from typing import Optional
from sqlmodel import Field, SQLModel

# China time helper for consistent timestamps (UTC+8)
from app.utils.timezone import china_now


class Key(SQLModel, table=True):
    __tablename__ = "keys"

    id: Optional[int] = Field(default=None, primary_key=True)
    provider: str = Field(index=True)
    key: str
    origin: Optional[str] = None
    tier: Optional[str] = None
    models: Optional[str] = None      # JSON list, e.g. '["gpt-4o","gpt-4"]'
    status_code: Optional[int] = None # 200/401/429/… from last verify
    create_time: datetime = Field(default_factory=china_now)
    notes: Optional[str] = None
