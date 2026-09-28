"""数据库会话与初始化。"""
from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.db.models import Base

Path(settings.upload_dir).mkdir(parents=True, exist_ok=True)

engine = create_async_engine(
    settings.database_url,
    echo=False,
    future=True,
    connect_args={"check_same_thread": False},
)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_add_missing_columns)


def _add_missing_columns(sync_conn) -> None:
    """轻量迁移：给已有表补上新增的列。

    create_all 只建新表，不会给旧表加列。老用户升级后，模型里新加的字段
    在库里不存在，一查询就报 no such column。这里对比模型与实际表结构，
    缺什么补什么，只做 ADD COLUMN，不删不改，已有数据不受影响。
    """
    from sqlalchemy import inspect, text

    insp = inspect(sync_conn)
    existing_tables = set(insp.get_table_names())
    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue
        have = {c["name"] for c in insp.get_columns(table.name)}
        for col in table.columns:
            if col.name in have:
                continue
            col_type = col.type.compile(dialect=sync_conn.dialect)
            default = ""
            if col.default is not None and getattr(col.default, "is_scalar", False):
                v = col.default.arg
                if isinstance(v, bool):
                    default = f" DEFAULT {1 if v else 0}"
                elif isinstance(v, (int, float)):
                    default = f" DEFAULT {v}"
                elif isinstance(v, str):
                    default = " DEFAULT '" + v.replace("'", "''") + "'"
            sync_conn.execute(text(
                f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {col_type}{default}'))
            print(f"[迁移] {table.name} 新增列 {col.name}")



async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session
