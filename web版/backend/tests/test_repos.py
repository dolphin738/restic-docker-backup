"""仓储读回归一专题测试：prune_interval_days 的 0 值陷阱回归护栏。

背景：两处仓储的读回归一曾写成 ``int(row["prune_interval_days"] or 7)``——
Python 里 ``0 or 7 == 7``，用户经 API 显式保存 0（语义=关闭 prune 到期提示，
``SchedulerService._due_for_prune`` 的 ``<=0`` 分支接管）后读回变成 7，
提示关不掉。修复为显式 ``is None`` 判断，本文件锁死该不变量。
"""

from __future__ import annotations

from app.db.database import Database
from app.db.repositories.task_repo import TaskRepo


async def test_task_prune_interval_zero_roundtrips(tmp_path):
    """TaskRepo：create 显式写 0 → 读回 0（create 返回与 get 双重断言）。"""
    db = Database(tmp_path / "task-zero.db")
    await db.connect()
    repo = TaskRepo(db)

    task = await repo.create(
        {"name": "关闭提示任务", "sources": ["/host/etc"], "prune_interval_days": 0}
    )
    assert task.prune_interval_days == 0  # create 返回值

    fetched = await repo.get(task.id)
    assert fetched is not None
    assert fetched.prune_interval_days == 0  # 读回归一（陷阱原发点）

    await db.close()


async def test_task_prune_interval_update_to_zero(tmp_path):
    """TaskRepo：update 改 0 → 读回 0（E35' 路径：用户事后关闭提示）。"""
    db = Database(tmp_path / "task-upd.db")
    await db.connect()
    repo = TaskRepo(db)

    task = await repo.create({"name": "先开后关", "sources": ["/host/etc"]})
    assert task.prune_interval_days == 7  # 缺省路径不受修复影响

    updated = await repo.update(task.id, {"prune_interval_days": 0})
    assert updated is not None
    assert updated.prune_interval_days == 0

    fetched = await repo.get(task.id)
    assert fetched is not None
    assert fetched.prune_interval_days == 0

    await db.close()
