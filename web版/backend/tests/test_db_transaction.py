"""候选 1 批次 0：``Database.transaction()`` 的契约测试。

覆盖决策 2 的三条硬约束：①多语句原子提交；②异常整体回滚；③事务中调 ``write()``
抛 ``RuntimeError``（防静默腰斩）；外加④不支持嵌套。
"""

from __future__ import annotations

import asyncio

import pytest

from app.db.database import Database


async def _new_db(tmp_path) -> Database:
    """建一个带测试表的临时库。"""
    db = Database(tmp_path / "tx.db")
    await db.connect()
    await db.write("CREATE TABLE IF NOT EXISTS t (id TEXT PRIMARY KEY, n INTEGER)")
    return db


async def _count(db: Database) -> int:
    row = await db.fetch_one("SELECT COUNT(*) AS c FROM t")
    return int(row["c"]) if row else 0


async def test_transaction_commits_all_statements(tmp_path):
    """事务内多条语句整体提交。"""
    db = await _new_db(tmp_path)
    try:
        async with db.transaction() as tx:
            await tx.execute("INSERT INTO t (id, n) VALUES (?, ?)", ("a", 1))
            await tx.execute("INSERT INTO t (id, n) VALUES (?, ?)", ("b", 2))
        assert await _count(db) == 2
    finally:
        await db.close()


async def test_transaction_rolls_back_on_exception(tmp_path):
    """异常整体回滚：一条都不留。"""
    db = await _new_db(tmp_path)
    try:
        with pytest.raises(RuntimeError, match="boom"):
            async with db.transaction() as tx:
                await tx.execute("INSERT INTO t (id, n) VALUES (?, ?)", ("a", 1))
                raise RuntimeError("boom")
        assert await _count(db) == 0
    finally:
        await db.close()


async def test_write_inside_transaction_raises(tmp_path):
    """事务中调 write() 抛 RuntimeError——否则会把事务静默腰斩且不报错。"""
    db = await _new_db(tmp_path)
    try:
        with pytest.raises(RuntimeError, match="事务进行中"):
            async with db.transaction():
                await db.write("INSERT INTO t (id, n) VALUES (?, ?)", ("a", 1))
    finally:
        await db.close()


async def test_nested_transaction_rejected(tmp_path):
    """不支持嵌套事务。"""
    db = await _new_db(tmp_path)
    try:
        with pytest.raises(RuntimeError, match="嵌套"):
            async with db.transaction():
                async with db.transaction():
                    pass
    finally:
        await db.close()


async def test_transaction_reusable_after_failure(tmp_path):
    """回滚后连接仍可用：标志复位、后续写正常。"""
    db = await _new_db(tmp_path)
    try:
        with pytest.raises(RuntimeError):
            async with db.transaction() as tx:
                await tx.execute("INSERT INTO t (id, n) VALUES (?, ?)", ("a", 1))
                raise RuntimeError("boom")
        assert db._in_transaction is False
        await db.write("INSERT INTO t (id, n) VALUES (?, ?)", ("b", 2))
        assert await _count(db) == 1
    finally:
        await db.close()


async def test_concurrent_transactions_are_serialized(tmp_path):
    """双并发事务：单写连接上各自独立提交，结果自洽（不丢、不叠）。"""
    db = await _new_db(tmp_path)
    try:

        async def one(idx: int) -> None:
            async with db.transaction() as tx:
                await tx.execute("INSERT INTO t (id, n) VALUES (?, ?)", (f"k{idx}", idx))

        await asyncio.gather(one(1), one(2))
        assert await _count(db) == 2
    finally:
        await db.close()
