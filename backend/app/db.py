"""工作台本地数据库(SQLite + SQLAlchemy)。

数据文件: backend/data/app.db
原白板 sessions/leads 的 JSON 存储不受影响,两套并存。
"""

from __future__ import annotations

import os

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings
from app.paths import DATA_DIR

os.makedirs(DATA_DIR, exist_ok=True)

DB_PATH = os.path.join(DATA_DIR, "app.db")

engine = create_engine(
    f"sqlite:///{DB_PATH}",
    connect_args={"check_same_thread": False},
)


@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):  # noqa: ANN001
    # WAL 提升并发读写;FTS5 建表时需要
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def init_db() -> None:
    from app import db_models  # noqa: F401  确保模型注册

    Base.metadata.create_all(engine)
    _migrate()


def _migrate() -> None:
    """轻量迁移:为已存在的旧表补新增列(create_all 不会改已有表)。"""
    migrations = {
        "tasks": {
            "created_by": "ALTER TABLE tasks ADD COLUMN created_by VARCHAR(32)",
        },
    }
    with engine.connect() as conn:
        for table, cols in migrations.items():
            rows = conn.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
            existing = {r[1] for r in rows}
            if not existing:
                continue
            for col, ddl in cols.items():
                if col not in existing:
                    conn.exec_driver_sql(ddl)
        conn.commit()


def get_db() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
