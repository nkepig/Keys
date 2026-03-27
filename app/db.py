from sqlmodel import SQLModel, create_engine, Session
from app.config import settings

engine = create_engine(settings.database_url, echo=settings.debug)


def init_db():
    SQLModel.metadata.create_all(engine)
    _migrate()


def _migrate():
    """为已有表补充新列（幂等）"""
    from sqlalchemy import text
    new_cols = [
        "ALTER TABLE keys ADD COLUMN status_code INTEGER",
        "ALTER TABLE keys DROP COLUMN status",  # 旧字段清理（若存在）
    ]
    with engine.connect() as conn:
        for sql in new_cols:
            try:
                conn.execute(text(sql))
                conn.commit()
            except Exception:
                pass  # 列已存在或不需要时忽略


def get_session():
    with Session(engine) as session:
        yield session
