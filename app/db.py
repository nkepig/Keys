from sqlmodel import SQLModel, create_engine, Session
from app.config import settings

engine = create_engine(settings.database_url, echo=settings.debug)


def init_db():
    SQLModel.metadata.create_all(engine)
    _migrate()


def _migrate():
    """为已有表补充新列（幂等）"""
    from sqlalchemy import text

    with engine.connect() as conn:
        key_cols = [row[1] for row in conn.execute(text("PRAGMA table_info(keys)")).fetchall()]
        if key_cols:
            if "status_code" not in key_cols:
                conn.execute(text("ALTER TABLE keys ADD COLUMN status_code INTEGER"))
                conn.commit()
            # 兼容旧 SQLite：不再主动 DROP COLUMN status
        scan_history_cols = [row[1] for row in conn.execute(text("PRAGMA table_info(scan_history)")).fetchall()]
        if scan_history_cols and "match_type" not in scan_history_cols:
            conn.execute(text("ALTER TABLE scan_history RENAME TO scan_history_legacy"))
            conn.commit()
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS scan_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    target TEXT NOT NULL,
                    netloc TEXT NOT NULL,
                    match_type TEXT NOT NULL DEFAULT 'target',
                    scanned_at TEXT NOT NULL DEFAULT (datetime('now', '+8 hours')),
                    UNIQUE(source, match_type, target)
                )
                """
            )
        )
        conn.commit()
        legacy_exists = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name='scan_history_legacy'")
        ).fetchone()
        if legacy_exists:
            conn.execute(
                text(
                    """
                    INSERT OR REPLACE INTO scan_history (source, target, netloc, match_type, scanned_at)
                    SELECT source, COALESCE(netloc, target), COALESCE(netloc, target), 'netloc', scanned_at
                    FROM scan_history_legacy
                    """
                )
            )
            conn.commit()
            conn.execute(text("DROP TABLE scan_history_legacy"))
            conn.commit()
        ddl = [
            "CREATE INDEX IF NOT EXISTS idx_scan_history_scanned_at ON scan_history(scanned_at)",
            "CREATE INDEX IF NOT EXISTS idx_scan_history_source_type_time ON scan_history(source, match_type, scanned_at)",
            "DELETE FROM scan_history WHERE scanned_at < datetime('now', '+8 hours', '-7 days')",
        ]
        for sql in ddl:
            conn.execute(text(sql))
            conn.commit()


def get_session():
    with Session(engine) as session:
        yield session
