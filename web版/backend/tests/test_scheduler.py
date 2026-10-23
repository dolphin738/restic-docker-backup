"""SchedulerService 测试（增量设计 §4 / R-013，X3 owner 裁决提前补测）。

覆盖设计 §7-T04 验收口径的六个面：
1. ``reload`` 触发器重建（cron 变更重建、新增/删除同步增减）；
2. ``catchup_on_startup`` 补跑判据（当天已过点且无成功 → 补跑；当天有成功 → 不补跑；
   跨天边界；冲突走 scheduled 分支落 cancelled + ``job.skipped_conflict`` 审计）；
3. jitter 范围与「先记账后执行」顺序；
4. prune 判定与自动 forget（N3 裁决 2026-10-14 开放：到期自动提交；
   catchup/fire 链路本身仍只提交 backup——自动 forget 只挂在 backup 成功回调
   ``maybe_auto_forget`` 上；判定基准 = 距上次成功 forget 的间隔）；
5. ``_build_trigger`` 兜底（任务级非法 cron 与 config ``schedule.cron_default``
   坏值均退化为「仅手动」，WARNING 不崩溃）；
6. ``start`` / ``shutdown`` 生命周期时序。

风格约定：复用 ``test_state_machine`` 的 ``FakeRestic`` 替身；DB/任务/job 全部走
真实仓储与落库值断言；补跑/到期判定的「当前时刻」以冻结
``app.services.scheduler.datetime`` 的方式保证确定性（APScheduler 触发器本身
用真实时间，不受影响）。

时区（CI 复盘 X3）：调度语义（cron 解释、today_start 锚点）全部**时区相对**——
``make_scheduler`` 接受 ``monkeypatch`` 时把进程 ``TZ`` 与 ``get_settings`` 缓存
对齐到入参 ``tz``（CI 容器 ``TZ=Asia/Shanghai``，不允许依赖"本地恰好 UTC"）；
``next_fire_time`` 回退与 catchup 两条判据均以 ``TZ_CASES`` 参数化，UTC 与
Asia/Shanghai 两组各自断言该时区下的正确行为。
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from test_state_machine import BACKUP_LINES, FakeRestic, await_job, insert_job

from app.constants import (
    BEHAVIOR_CHECK_ENABLED_KEY,
    BEHAVIOR_CHECK_SUBSET_DENOMINATOR_KEY,
    BEHAVIOR_MONTHLY_FULL_DAY_KEY,
    BEHAVIOR_MONTHLY_FULL_ENABLED_KEY,
    DUMP_RETENTION_DAYS_KEY,
)
from app.db.database import Database
from app.db.repositories.audit_repo import AuditRepo
from app.db.repositories.config_repo import ConfigRepo
from app.db.repositories.job_repo import JobRepo
from app.db.repositories.task_repo import TaskRepo
from app.services import scheduler as scheduler_module
from app.services.job_runner import JobRunner
from app.services.parser import OutputParser
from app.services.retention import RetentionService
from app.services.scheduler import (
    SCHEDULE_CRON_DEFAULT_KEY,
    SCHEDULE_JITTER_DEFAULT_SECONDS,
    SCHEDULE_JITTER_KEY,
    SCHEDULE_JITTER_MAX_SECONDS,
    SchedulerService,
)
from app.settings import Settings, get_settings
from app.utils.time import now_utc, parse_iso, resolve_timezone, utc_iso

# 补跑/到期判定的冻结时刻：2026-10-13 12:00:00 UTC（周二）
FIXED_NOW = datetime(2026, 10, 13, 12, 0, 0, tzinfo=UTC)

# 时区参数化（CI 复盘）：CI 容器 TZ=Asia/Shanghai，调度语义（cron 解释、today_start）
# 全部时区相对——两组时区都必须有正确行为断言，不允许"统一成 UTC 把 CI 场景藏掉"
TZ_CASES = ["UTC", "Asia/Shanghai"]


# ---------------------------------------------------------------- 测试基座


async def make_scheduler(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch | None = None,
    *,
    lines: list[str] | None = None,
    exit_code: int = 0,
    tz: str = "UTC",
) -> tuple[Database, SchedulerService, TaskRepo, JobRepo, ConfigRepo, JobRunner]:
    """构造带真实仓储与离线 restic 替身的调度器（DB 落 tmp_path 隔离）。

    ``monkeypatch`` 传入时，先把进程 ``TZ`` 环境变量与 ``get_settings`` 缓存对齐到
    ``tz`` 再构造（CI 复盘：CI 容器 ``TZ=Asia/Shanghai``——时区必须显式控制，
    不能依赖"本地恰好 UTC"）。缺省 None 时仍以显式 ``Settings(tz=...)`` 构造
    （init 优先级高于 env，实测验证），行为不变。
    """
    if monkeypatch is not None:
        monkeypatch.setenv("TZ", tz)
        get_settings.cache_clear()
    db = Database(tmp_path / "sched.db")
    await db.connect()
    settings = Settings(data_dir=str(tmp_path), tz=tz)
    settings.jobs_dir.mkdir(parents=True, exist_ok=True)
    task_repo = TaskRepo(db)
    job_repo = JobRepo(db)
    config_repo = ConfigRepo(db)
    audit_repo = AuditRepo(db)
    runner = JobRunner(
        db,
        FakeRestic(lines=lines, exit_code=exit_code),  # type: ignore[arg-type]
        OutputParser(),
        audit_repo,
        settings=settings,
        task_loader=task_repo.get_dict,
    )
    retention = RetentionService(job_repo, config_repo, audit_repo, settings)
    scheduler = SchedulerService(
        task_repo, runner, config_repo, settings=settings, jobs=job_repo, retention=retention
    )
    return db, scheduler, task_repo, job_repo, config_repo, runner


async def create_task(task_repo: TaskRepo, name: str, **overrides) -> object:
    """直写仓储建任务（绕过 API 校验——兜底测试需要模拟运维直写的坏值）。"""
    values: dict = {"name": name, "enabled": 1, "sources": ["/sources/srv"], "excludes": []}
    values.update(overrides)
    return await task_repo.create(values)


async def insert_success_job(
    job_repo: JobRepo, job_id: str, task_id: int, finished_at: str, *, job_type: str = "backup"
) -> None:
    """落一条成功 job（带 finished_at）——补跑/prune 判定的历史数据。"""
    await job_repo.create(
        job_id=job_id,
        job_type=job_type,
        trigger="scheduled",
        log_path=f"/tmp/{job_id}.log",
        status="success",
        task_id=task_id,
    )
    await job_repo.update_status(job_id, "success", finished_at=finished_at)


def _job_ids(scheduler: SchedulerService) -> set[str]:
    """当前调度器内的触发器 id 集合（``backup-task-<id>``）。"""
    return {str(job.id) for job in scheduler._scheduler.get_jobs()}  # noqa: SLF001


def _backup_jobs(scheduler: SchedulerService) -> list[object]:
    """当前调度器内 ``backup-task-`` 域的触发器（start 后另有 maintenance-* 域，需分域断言）。"""
    return [job for job in scheduler._scheduler.get_jobs() if str(job.id).startswith("backup-task-")]  # noqa: SLF001


def _backup_job_ids(scheduler: SchedulerService) -> set[str]:
    """``backup-task-`` 域触发器 id 集合（reload 现在会补挂 maintenance-* 域，断言须分域）。"""
    return {str(job.id) for job in _backup_jobs(scheduler)}


# ---------------------------------------------------------------- 1. reload 触发器重建


async def test_reload_creates_trigger_per_enabled_task(tmp_path):
    """enabled=1 且有效 cron 非空 → 各建一个触发器；无 cron/暂停 → 不建。"""
    db, scheduler, task_repo, _, _, _ = await make_scheduler(tmp_path)
    task_a = await create_task(task_repo, "A", cron="0 3 * * *")
    await create_task(task_repo, "B", cron="0 4 * * *", enabled=0)
    await create_task(task_repo, "C")  # 无 cron → 仅手动

    await scheduler.reload()

    assert _backup_job_ids(scheduler) == {f"backup-task-{task_a.id}"}
    job = _backup_jobs(scheduler)[0]  # noqa: SLF001
    assert job.args == (task_a.id,)
    assert job.func == scheduler._fire_backup
    await db.close()


async def test_reload_rebuilds_trigger_when_cron_changes(tmp_path, monkeypatch):
    """cron 变更后 reload → 触发器随之重建（下次触发时间按新 cron 算，US-026-02 AC1）。

    在 **RUNNING 态**断言（生产时序：``start()`` 内 ``scheduler.start()`` 先于
    ``reload()``，路由后续 reload 亦在运行态）——``replace_existing`` 在该态经
    jobstore ``update_job`` 真正去重；STOPPED 态 add_job 只进 APScheduler 的
    pending 列表（start 时自愈），两者语义不同，见实现说明。
    """
    db, scheduler, task_repo, _, _, _ = await make_scheduler(tmp_path)
    task = await create_task(task_repo, "cron 变更", cron="0 18 * * *")
    monkeypatch.setattr(scheduler_module, "datetime", _make_fixed_datetime(FIXED_NOW))
    await scheduler.start()  # RUNNING；18:00 未到点，catchup 不提交

    reference = FIXED_NOW
    backup_jobs = _backup_jobs(scheduler)  # noqa: SLF001
    assert len(backup_jobs) == 1
    first_next = backup_jobs[0].trigger.get_next_fire_time(None, reference)
    assert first_next.hour == 18

    await task_repo.update(task.id, {"cron": "0 19 * * *"})
    await scheduler.reload()

    jobs = _backup_jobs(scheduler)  # noqa: SLF001
    assert len(jobs) == 1  # replace_existing：不重复堆积
    second_next = jobs[0].trigger.get_next_fire_time(None, reference)
    assert second_next.hour == 19
    assert first_next != second_next

    await scheduler.shutdown()
    await asyncio.sleep(0)
    await db.close()


async def test_reload_syncs_job_set_on_create_and_delete(tmp_path):
    """新增/暂停/删除任务 → 触发器集合同步增减（全量重建语义）。"""
    db, scheduler, task_repo, _, _, _ = await make_scheduler(tmp_path)
    task_a = await create_task(task_repo, "A", cron="0 3 * * *")
    await scheduler.reload()
    assert _backup_job_ids(scheduler) == {f"backup-task-{task_a.id}"}

    task_b = await create_task(task_repo, "B", cron="0 4 * * *")
    await scheduler.reload()
    assert _backup_job_ids(scheduler) == {f"backup-task-{task_a.id}", f"backup-task-{task_b.id}"}

    await task_repo.update(task_b.id, {"enabled": 0})
    await scheduler.reload()
    assert _backup_job_ids(scheduler) == {f"backup-task-{task_a.id}"}

    await task_repo.update(task_b.id, {"enabled": 1})
    await scheduler.reload()
    assert _backup_job_ids(scheduler) == {f"backup-task-{task_a.id}", f"backup-task-{task_b.id}"}

    await task_repo.delete(task_b.id)
    await scheduler.reload()
    assert _backup_job_ids(scheduler) == {f"backup-task-{task_a.id}"}
    await db.close()


# ---------------------------------------------------------------- next_fire_time 推导


async def test_next_fire_time_enabled_task_with_cron(tmp_path):
    """有效 cron → ISO 8601 UTC 的未来时刻（E34 卡片「下次运行」）。"""
    db, scheduler, task_repo, _, _, _ = await make_scheduler(tmp_path)
    task = await create_task(task_repo, "推导", cron="0 3 * * *")
    await scheduler.reload()

    nxt = scheduler.next_fire_time(task)
    assert nxt is not None
    parsed = parse_iso(nxt)
    assert parsed is not None and parsed > now_utc()
    await db.close()


async def test_next_fire_time_disabled_or_manual_returns_none(tmp_path):
    """已暂停 / 无有效 cron → None（UI：已暂停 / 仅手动）。"""
    db, scheduler, task_repo, _, _, _ = await make_scheduler(tmp_path)
    paused = await create_task(task_repo, "暂停", cron="0 3 * * *", enabled=0)
    manual = await create_task(task_repo, "手动")  # 无 cron、无全局缺省
    await scheduler.reload()

    assert scheduler.next_fire_time(paused) is None
    assert scheduler.next_fire_time(manual) is None
    await db.close()


@pytest.mark.parametrize("tz", TZ_CASES)
async def test_next_fire_time_falls_back_to_config_cron_default(tmp_path, monkeypatch, tz):
    """任务 cron 为空 → 回退 config schedule.cron_default（A8 基线；变更后 reload 生效）。

    时区参数化（CI 复盘 X3）：cron 在**任务时区**内解释——UTC 组下次触发 05:00 UTC，
    Asia/Shanghai 组 05:00 CST（=21:00 UTC）。断言口径与 tz 无关：换算回该时区后
    小时数应为 cron 声明值，且必须是未来时刻。
    """
    db, scheduler, task_repo, _, config_repo, _ = await make_scheduler(tmp_path, monkeypatch, tz=tz)
    task = await create_task(task_repo, "全局缺省")

    await config_repo.set(SCHEDULE_CRON_DEFAULT_KEY, "0 5 * * *")
    await scheduler.reload()
    first = parse_iso(scheduler.next_fire_time(task))
    assert first is not None
    # 换算回任务时区必须用 resolve_timezone（CI 复盘 cnb-dgc-1k4qqhg77：
    # 直接 ZoneInfo(tz) 在 tzdata 异常的容器里 utcoffset 非零，UTC 组被平移 8h 误报）
    assert first.astimezone(resolve_timezone(tz)).hour == 5
    assert first > now_utc()

    await config_repo.set(SCHEDULE_CRON_DEFAULT_KEY, "0 6 * * *")
    await scheduler.reload()
    second = parse_iso(scheduler.next_fire_time(task))
    assert second is not None
    assert second.astimezone(resolve_timezone(tz)).hour == 6
    await db.close()


# ---------------------------------------------------------------- 2. catchup 补跑判据


async def test_catchup_submits_when_point_passed_without_success(tmp_path, monkeypatch):
    """当天已过点且无成功 backup → 补跑一次（走 scheduled 分支，不排队）。"""
    db, scheduler, task_repo, job_repo, _, runner = await make_scheduler(tmp_path)
    task = await create_task(task_repo, "补跑任务", cron="0 3 * * *")  # 当天 03:00 已过（冻结在 12:00）
    monkeypatch.setattr(scheduler_module, "datetime", _make_fixed_datetime(FIXED_NOW))

    await scheduler.catchup_on_startup()

    jobs, total = await job_repo.list_page(task_id=task.id)
    assert total == 1
    assert jobs[0].trigger == "scheduled"
    await await_job(runner, jobs[0].id)
    await db.close()


async def test_catchup_skips_when_success_after_todays_point(tmp_path, monkeypatch):
    """当天已过点但 03:00 后已有成功 backup → 不补跑。"""
    db, scheduler, task_repo, job_repo, _, _ = await make_scheduler(tmp_path)
    task = await create_task(task_repo, "已成功", cron="0 3 * * *")
    await insert_success_job(job_repo, "job-ok-today", task.id, "2026-10-13T03:30:00Z")
    monkeypatch.setattr(scheduler_module, "datetime", _make_fixed_datetime(FIXED_NOW))

    await scheduler.catchup_on_startup()

    assert (await job_repo.list_page(task_id=task.id))[1] == 1  # 仍只有那条历史成功
    await db.close()


@pytest.mark.parametrize(("tz", "expected_jobs"), [("UTC", 0), ("Asia/Shanghai", 1)])
async def test_catchup_due_point_is_timezone_relative(tmp_path, monkeypatch, tz, expected_jobs):
    """「当天应触发点是否已过」是**时区相对**的（CI 复盘 X3）：冻结 now=12:00 UTC。

    UTC：今天 18:00 UTC 未到 → 不补跑（0 条）；Asia/Shanghai：今天 18:00 CST
    （=10:00 UTC）已过 → 补跑（1 条）。生产语义两者都正确——cron 与 today_start
    均在 config.timezone→settings.tz 内解释（设计 §4.1）。
    """
    db, scheduler, task_repo, job_repo, _, runner = await make_scheduler(tmp_path, monkeypatch, tz=tz)
    task = await create_task(task_repo, "时区相对", cron="0 18 * * *")
    monkeypatch.setattr(scheduler_module, "datetime", _make_fixed_datetime(FIXED_NOW))

    await scheduler.catchup_on_startup()

    jobs, total = await job_repo.list_page(task_id=task.id)
    assert total == expected_jobs
    for job in jobs:  # 收尾后台执行任务（CI teardown 噪声修复：先 await 再 close）
        await await_job(runner, job.id)
    await db.close()


@pytest.mark.parametrize(("tz", "expected_total"), [("UTC", 2), ("Asia/Shanghai", 1)])
async def test_catchup_cross_day_boundary_is_timezone_relative(tmp_path, monkeypatch, tz, expected_total):
    """跨天边界（时区相对，CI 复盘 X3）：前一日 23:30 UTC 的成功是否算「今天已成功」
    取决于 today_start 的时区锚点。

    UTC：today_start=10-13 00:00 UTC，成功（10-12 23:30 UTC）更早 → 不抑制 → 补跑（共 2 条）；
    Asia/Shanghai：today_start=10-13 00:00 CST（=10-12 16:00 UTC），成功（10-12 23:30 UTC
    =10-13 07:30 CST）已落在今天 → 抑制 → 不补跑（仍 1 条）。
    """
    db, scheduler, task_repo, job_repo, _, runner = await make_scheduler(tmp_path, monkeypatch, tz=tz)
    task = await create_task(task_repo, "跨天", cron="0 0 * * *")  # 当天 00:00 已过
    await insert_success_job(job_repo, "job-ok-yesterday", task.id, "2026-10-12T23:30:00Z")
    monkeypatch.setattr(scheduler_module, "datetime", _make_fixed_datetime(FIXED_NOW))

    await scheduler.catchup_on_startup()

    jobs, total = await job_repo.list_page(task_id=task.id)
    assert total == expected_total
    for job in jobs:  # 收尾后台执行任务（历史成功行无 _run 任务，await_job 自动跳过）
        await await_job(runner, job.id)
    await db.close()


async def test_catchup_skips_disabled_task(tmp_path, monkeypatch):
    """已暂停任务不补跑（enabled=0）。"""
    db, scheduler, task_repo, job_repo, _, _ = await make_scheduler(tmp_path)
    await create_task(task_repo, "暂停不补跑", cron="0 3 * * *", enabled=0)
    monkeypatch.setattr(scheduler_module, "datetime", _make_fixed_datetime(FIXED_NOW))

    await scheduler.catchup_on_startup()

    assert (await job_repo.list_page())[1] == 0
    await db.close()


async def test_catchup_conflict_lands_cancelled_and_audit(tmp_path, monkeypatch):
    """补跑遇运行中任务 → scheduled 冲突分支：cancelled 任务 + job.skipped_conflict 审计，不排队。"""
    db, scheduler, task_repo, job_repo, _, _ = await make_scheduler(tmp_path)
    task = await create_task(task_repo, "冲突补跑", cron="0 3 * * *")
    await insert_job(job_repo, "job-active", "running")  # 无 task_id 的活跃任务占位
    monkeypatch.setattr(scheduler_module, "datetime", _make_fixed_datetime(FIXED_NOW))

    await scheduler.catchup_on_startup()

    jobs, _ = await job_repo.list_page()
    skipped = [job for job in jobs if job.status == "cancelled"]
    assert len(skipped) == 1
    assert skipped[0].trigger == "scheduled"
    assert skipped[0].task_id == task.id

    audit = AuditRepo(db)
    rows, total = await audit.query(action="job.skipped_conflict")
    assert total == 1
    assert rows[0].result == "denied"
    await db.close()


# ---------------------------------------------------------------- 3. jitter 与记账顺序


async def test_jitter_seconds_default_clamping_and_invalid(tmp_path):
    """jitter 读取：缺省 300、负值钳 0、爆值钳 3600、非数字回退缺省。"""
    db, scheduler, _, _, config_repo, _ = await make_scheduler(tmp_path)

    assert await scheduler._jitter_seconds() == SCHEDULE_JITTER_DEFAULT_SECONDS  # noqa: SLF001

    await config_repo.set(SCHEDULE_JITTER_KEY, "0")
    assert await scheduler._jitter_seconds() == 0  # noqa: SLF001

    await config_repo.set(SCHEDULE_JITTER_KEY, "-5")
    assert await scheduler._jitter_seconds() == 0  # noqa: SLF001

    await config_repo.set(SCHEDULE_JITTER_KEY, "99999")
    assert await scheduler._jitter_seconds() == SCHEDULE_JITTER_MAX_SECONDS  # noqa: SLF001

    await config_repo.set(SCHEDULE_JITTER_KEY, "abc")
    assert await scheduler._jitter_seconds() == SCHEDULE_JITTER_DEFAULT_SECONDS  # noqa: SLF001

    await config_repo.set(SCHEDULE_JITTER_KEY, "")
    assert await scheduler._jitter_seconds() == SCHEDULE_JITTER_DEFAULT_SECONDS  # noqa: SLF001
    await db.close()


async def test_fire_backup_books_last_run_at_before_submit(tmp_path):
    """触发链顺序：jitter(0) 之后**先记账**（last_run_at 落库）再 submit（§6.4 防重复触发）。"""
    db, scheduler, task_repo, _, config_repo, runner = await make_scheduler(tmp_path)
    task = await create_task(task_repo, "记账顺序", cron="0 3 * * *")
    await config_repo.set(SCHEDULE_JITTER_KEY, "0")  # 去掉随机睡眠，保证测试确定性

    submitted: list[tuple[dict, str | None, object]] = []
    original_submit = runner.submit

    async def recording_submit(job_type, **kwargs):  # noqa: ANN001, ANN202
        current = await task_repo.get(kwargs["task_id"])
        job = await original_submit(job_type, **kwargs)
        submitted.append((kwargs, current.last_run_at if current else None, job))
        return job

    runner.submit = recording_submit

    await scheduler._fire_backup(task.id)  # noqa: SLF001

    assert len(submitted) == 1
    kwargs, last_run_at_at_submit, job = submitted[0]
    assert kwargs == {"repo_id": "default", "task_id": task.id, "trigger": "scheduled"}
    assert last_run_at_at_submit is not None  # submit 发生时记账已落库
    assert (await task_repo.get(task.id)).last_run_at is not None

    await await_job(runner, job.id)  # type: ignore[attr-defined]
    await db.close()


async def test_fire_backup_skips_deleted_task(tmp_path):
    """触发时任务已删除 → 跳过（不提交、不崩溃；触发器残留由 reload 清理）。"""
    db, scheduler, task_repo, job_repo, _, _ = await make_scheduler(tmp_path)
    task = await create_task(task_repo, "将删除", cron="0 3 * * *")
    await task_repo.delete(task.id)

    await scheduler._fire_backup(task.id)  # noqa: SLF001

    assert (await job_repo.list_page())[1] == 0
    await db.close()


async def test_fire_backup_skips_disabled_task(tmp_path):
    """触发时任务已暂停 → enabled 复核跳过（不提交）。"""
    db, scheduler, task_repo, job_repo, _, _ = await make_scheduler(tmp_path)
    task = await create_task(task_repo, "将暂停", cron="0 3 * * *", enabled=0)

    await scheduler._fire_backup(task.id)  # noqa: SLF001

    assert (await job_repo.list_page())[1] == 0
    await db.close()


# ---------------------------------------------------------------- 4. prune 判定与自动 forget（N3）


async def test_due_for_prune_never_submits_forget(tmp_path, monkeypatch):
    """catchup 与 fire 链路全程只提交 backup——自动 forget 只挂在 backup 成功回调。

    N3 裁决（2026-10-14）：自动 forget 由 ``maybe_auto_forget`` 在 backup success
    终态回调里触发，**不在** catchup/fire 链路内（回归护栏）。
    """
    db, scheduler, task_repo, job_repo, config_repo, runner = await make_scheduler(tmp_path)
    task = await create_task(task_repo, "prune 到期", cron="0 3 * * *", prune_interval_days=7)
    # 从未 forget 过 + 有成功 backup → 首次到期（新语义）。
    # 基准必须锚定 FIXED_NOW（调度器被冻结到该时刻）而非真实 now_utc()——否则随着真实日期
    # 推移，now_utc()-10d 会越过 FIXED_NOW 当天的触发点，catchup 误判「当天已成功」而跳过
    # （date-bomb：2026-10-24 曾因此由绿转红）。
    await insert_success_job(job_repo, "job-old-ok", task.id, utc_iso(FIXED_NOW - timedelta(days=10)))
    await config_repo.set(SCHEDULE_JITTER_KEY, "0")  # 去掉随机睡眠，保证测试确定性

    assert await scheduler._due_for_prune(task) is True

    submitted: list[tuple[str, object]] = []
    original_submit = runner.submit

    async def recording_submit(job_type, **kwargs):  # noqa: ANN001, ANN202
        job = await original_submit(job_type, **kwargs)
        submitted.append((job_type, job))
        return job

    runner.submit = recording_submit
    monkeypatch.setattr(scheduler_module, "datetime", _make_fixed_datetime(FIXED_NOW))

    await scheduler.catchup_on_startup()
    await scheduler._fire_backup(task.id)  # noqa: SLF001

    # 两条链路只提交 backup（自动 forget 不在链路内）
    assert [job_type for job_type, _ in submitted] == ["backup", "backup"]
    jobs, _ = await job_repo.list_page()
    assert all(job.type != "forget" for job in jobs)

    for _, job in submitted:  # 收尾后台执行任务，避免跨测试泄漏
        await await_job(runner, job.id)  # type: ignore[attr-defined]
    await db.close()


async def test_due_for_prune_semantics(tmp_path):
    """到期判定（2026-10-14 语义修正）：基准 = 距上次成功 forget 的间隔。

    - 从未 forget + 有成功 backup → True（首次到期）；
    - 从未 forget + 从未成功 backup → False（无可清理对象）；
    - 近期刚成功 forget → False；
    - 上次 forget 超间隔 → True；
    - prune_interval_days=0 → False（显式不启用）。
    """
    db, scheduler, task_repo, job_repo, _, _ = await make_scheduler(tmp_path)

    first = await create_task(task_repo, "首次到期", prune_interval_days=7)
    await insert_success_job(job_repo, "job-first-bk", first.id, utc_iso(now_utc() - timedelta(days=10)))
    assert await scheduler._due_for_prune(first) is True

    bare = await create_task(task_repo, "从未成功", prune_interval_days=7)
    assert await scheduler._due_for_prune(bare) is False

    recent = await create_task(task_repo, "近期清理过", prune_interval_days=7)
    await insert_success_job(job_repo, "job-rc-bk", recent.id, utc_iso(now_utc() - timedelta(days=10)))
    await insert_success_job(
        job_repo, "job-rc-fg", recent.id, utc_iso(now_utc() - timedelta(days=1)), job_type="forget"
    )
    assert await scheduler._due_for_prune(recent) is False

    stale = await create_task(task_repo, "清理已过期", prune_interval_days=7)
    await insert_success_job(
        job_repo, "job-st-fg", stale.id, utc_iso(now_utc() - timedelta(days=8)), job_type="forget"
    )
    assert await scheduler._due_for_prune(stale) is True

    zero_row = await create_task(task_repo, "间隔为零", prune_interval_days=0)
    await insert_success_job(job_repo, "job-zr-bk", zero_row.id, utc_iso(now_utc() - timedelta(days=99)))
    assert await scheduler._due_for_prune(zero_row) is False
    await db.close()


async def test_maybe_auto_forget_submits_when_due(tmp_path):
    """到期 + 有保留规则 → 自动提交 forget（scheduled + prune=True，keep 规则经 profile）。"""
    db, scheduler, task_repo, job_repo, _, runner = await make_scheduler(tmp_path)
    task = await create_task(task_repo, "自动清理", prune_interval_days=7, keep_daily=7)
    await insert_success_job(job_repo, "job-auto-bk", task.id, utc_iso(now_utc() - timedelta(days=10)))

    submitted: list[tuple[str, dict]] = []
    forget_jobs: list[object] = []
    original_submit = runner.submit

    async def recording_submit(job_type, **kwargs):  # noqa: ANN001, ANN202
        job = await original_submit(job_type, **kwargs)
        submitted.append((job_type, kwargs))
        if job_type == "forget":
            forget_jobs.append(job)
        return job

    runner.submit = recording_submit
    await scheduler.maybe_auto_forget(task.id)

    assert [job_type for job_type, _ in submitted] == ["forget"]
    assert submitted[0][1]["task_id"] == task.id
    assert submitted[0][1]["trigger"] == "scheduled"
    assert submitted[0][1]["params"] == {"prune": True}

    # 执行链真实跑通：keep 规则来自任务 profile，tags 来自任务 tag
    if forget_jobs:
        await await_job(runner, forget_jobs[0].id)  # type: ignore[attr-defined]
    await db.close()


async def test_maybe_auto_forget_guards(tmp_path):
    """护栏：未到期 / 全零 keep 规则 / 间隔 0 / 已停用 → 一律不提交。"""
    db, scheduler, task_repo, job_repo, _, runner = await make_scheduler(tmp_path)
    submitted: list[str] = []
    original_submit = runner.submit

    async def recording_submit(job_type, **kwargs):  # noqa: ANN001, ANN202
        job = await original_submit(job_type, **kwargs)
        submitted.append(job_type)
        return job

    runner.submit = recording_submit

    # 1) 全零 keep 规则（默认建任务形态）→ 绝不自动裁剪（G3）
    no_rules = await create_task(task_repo, "无保留规则", prune_interval_days=7)
    await insert_success_job(job_repo, "job-nr-bk", no_rules.id, utc_iso(now_utc() - timedelta(days=10)))
    await scheduler.maybe_auto_forget(no_rules.id)
    assert submitted == []

    # 2) 近期刚 forget 过 → 未到期
    recent = await create_task(task_repo, "近期清理", prune_interval_days=7, keep_daily=7)
    await insert_success_job(
        job_repo, "job-rr-fg", recent.id, utc_iso(now_utc() - timedelta(days=1)), job_type="forget"
    )
    await scheduler.maybe_auto_forget(recent.id)
    assert submitted == []

    # 3) prune_interval_days=0 → 显式关闭自动清理
    off = await create_task(task_repo, "已关闭", prune_interval_days=0, keep_daily=7)
    await insert_success_job(job_repo, "job-of-bk", off.id, utc_iso(now_utc() - timedelta(days=99)))
    await scheduler.maybe_auto_forget(off.id)
    assert submitted == []

    # 4) 停用任务 → 不提交
    disabled = await create_task(task_repo, "已停用", prune_interval_days=7, keep_daily=7, enabled=0)
    await insert_success_job(job_repo, "job-ds-bk", disabled.id, utc_iso(now_utc() - timedelta(days=99)))
    await scheduler.maybe_auto_forget(disabled.id)
    assert submitted == []

    # 5) 不存在的任务 → 静默返回
    await scheduler.maybe_auto_forget(99999)
    assert submitted == []

    await db.close()


# ---------------------------------------------------------------- 5. _build_trigger 兜底


async def test_invalid_task_cron_degrades_to_manual_only(tmp_path, caplog):
    """任务级非法 cron（如 6 字段秒级）→ WARNING + None → 不建触发器、next_fire_time None。"""
    db, scheduler, task_repo, _, _, _ = await make_scheduler(tmp_path)
    task = await create_task(task_repo, "坏 cron 任务", cron="0 3 * * * *")  # 6 字段

    with caplog.at_level(logging.WARNING, logger="app.services.scheduler"):
        await scheduler.reload()
        nxt = scheduler.next_fire_time(task)

    assert nxt is None  # 退化「仅手动」
    assert _backup_job_ids(scheduler) == set()
    assert "无法解析" in caplog.text
    await db.close()


async def test_broken_config_cron_default_degrades_all_to_manual_only(tmp_path, caplog):
    """config schedule.cron_default 被运维直写坏（不经服务端校验）→ 兜底降级。

    该值是绕过 API 的运维直写值，此兜底是最后防线：坏掉时任务退化为仅手动、
    记 WARNING、**不崩溃**。
    """
    db, scheduler, task_repo, _, config_repo, _ = await make_scheduler(tmp_path)
    await config_repo.set(SCHEDULE_CRON_DEFAULT_KEY, "0 3 *")  # 3 字段坏值
    task = await create_task(task_repo, "全局坏缺省")  # 无任务级 cron

    with caplog.at_level(logging.WARNING, logger="app.services.scheduler"):
        await scheduler.reload()  # 不得抛异常
        nxt = scheduler.next_fire_time(task)

    assert nxt is None
    assert _backup_job_ids(scheduler) == set()
    assert "无法解析" in caplog.text
    await db.close()


async def test_valid_config_cron_default_still_builds_trigger(tmp_path):
    """对照组：config cron_default 合法 → 无任务级 cron 的任务照常建触发器。"""
    db, scheduler, task_repo, _, config_repo, _ = await make_scheduler(tmp_path)
    await config_repo.set(SCHEDULE_CRON_DEFAULT_KEY, "0 5 * * *")
    task = await create_task(task_repo, "全局好缺省")

    await scheduler.reload()

    assert _backup_job_ids(scheduler) == {f"backup-task-{task.id}"}
    await db.close()


# ---------------------------------------------------------------- 7. cron 星期字段标准语义（缺陷护栏）


def _next_fire_weekdays(trigger, tz_name: str, count: int = 6) -> list[int]:
    """连续取 ``count`` 个触发点，换算回任务时区后的星期序号（Monday=0 … Sunday=6）。

    触发点以「上一个触发点」为游标推进（APScheduler ``get_next_fire_time`` 语义），
    故工作日区间 ``1-5`` 取 6 个点必然覆盖周一至周五全部五天。
    """
    zone = resolve_timezone(tz_name)
    weekdays: list[int] = []
    previous = None
    now = datetime.now(UTC)
    for _ in range(count):
        moment = trigger.get_next_fire_time(previous, now if previous is None else previous)
        assert moment is not None
        weekdays.append(moment.astimezone(zone).weekday())
        previous = moment
    return weekdays


@pytest.mark.parametrize("tz", TZ_CASES)
@pytest.mark.parametrize(
    ("cron", "expected_weekdays"),
    [
        ("0 3 * * 1", {0}),  # 标准 crontab 1=周一（APScheduler 原生会落到周二）
        ("0 3 * * 2", {1}),  # 2=周二
        ("0 3 * * 6", {5}),  # 6=周六
        ("0 3 * * 0", {6}),  # 0=周日
        ("0 3 * * 7", {6}),  # 7=周日（与 0 同义，非越界）
        ("0 3 * * 1-5", {0, 1, 2, 3, 4}),  # 工作日（周一至周五）
    ],
)
async def test_cron_weekday_uses_standard_crontab_semantics(
    tmp_path, monkeypatch, tz, cron, expected_weekdays
):
    """星期字段必须按**标准 crontab** 语义解释：0/7=周日、1=周一 … 6=周六。

    护栏背景（真机缺陷）：APScheduler 3.x ``CronTrigger.from_crontab`` 存在官方承认的
    历史缺陷（docstring warning / issue #286）——其星期编号 0=周一，与标准 crontab 的
    0=周日正好差一天。真机任务 #1 配 ``cron="0 3 * * 1"``（管理员意图「每周一 03:00」），
    接口返回的下次运行落在**周二**，即该缺陷的实证。本产品 cron 由管理员直接填写、
    按标准 crontab 语义理解，故调度器必须换算（``(n + 6) % 7``）后再构造触发器。
    """
    db, scheduler, task_repo, _, _, _ = await make_scheduler(tmp_path, monkeypatch, tz=tz)
    task = await create_task(task_repo, "星期语义", cron=cron)
    await scheduler.reload()

    trigger = scheduler._build_trigger(task.id, cron)  # noqa: SLF001
    assert trigger is not None
    assert set(_next_fire_weekdays(trigger, tz)) == expected_weekdays

    # 卡片口径（next_fire_time）同样落在正确星期——真机任务 #1 的观察面
    nxt = parse_iso(scheduler.next_fire_time(task))
    assert nxt is not None
    assert nxt.astimezone(resolve_timezone(tz)).weekday() in expected_weekdays
    await db.close()


@pytest.mark.parametrize("tz", TZ_CASES)
async def test_cron_minute_step_unaffected_by_weekday_conversion(tmp_path, monkeypatch, tz):
    """对照组：``*/10 * * * *`` 无星期字段，换算不得改变其行为（整 10 分点、间隔 10 分钟）。"""
    db, scheduler, task_repo, _, _, _ = await make_scheduler(tmp_path, monkeypatch, tz=tz)
    task = await create_task(task_repo, "十分钟", cron="*/10 * * * *")
    await scheduler.reload()

    trigger = scheduler._build_trigger(task.id, "*/10 * * * *")  # noqa: SLF001
    assert trigger is not None
    zone = resolve_timezone(tz)
    previous = None
    now = datetime.now(UTC)
    for _ in range(3):
        moment = trigger.get_next_fire_time(previous, now if previous is None else previous)
        assert moment is not None
        local = moment.astimezone(zone)
        assert local.minute % 10 == 0
        if previous is not None:
            assert (moment - previous).total_seconds() == 600
        previous = moment
    await db.close()


@pytest.mark.parametrize(
    ("raw_weekday", "expected_expr"),
    [
        ("0", "6"),  # 周日
        ("1", "0"),  # 周一
        ("6", "5"),  # 周六
        ("7", "6"),  # 周日（与 0 同义）
        ("*", "*"),  # 通配符：两边同为「全部」，不变
        ("mon-fri", "mon-fri"),  # 名称写法两边语义一致，不换算
        ("sun", "sun"),  # 单个名称同理
        # 数字区间：先展开成标准星期集合再逐项换算——**不得对端点独立取模**
        # （``0-6`` 独立取模会变成 ``6-5``，首值大于末值撞 APScheduler ``first > last``
        # 校验抛 ValueError → 任务退化「仅手动」＝备份静默停止）
        ("1-5", "0,1,2,3,4"),  # 工作日
        ("0-1", "6,0"),  # 周日、周一（跨周日边界）
        ("0-6", "6,0,1,2,3,4,5"),  # 周日至周六 = 每天
        ("5-6", "4,5"),  # 周五、周六
        ("0-0", "6"),  # 仅周日
        ("6-6", "5"),  # 仅周六
        ("1-7", "0,1,2,3,4,5,6"),  # 周一至周日 = 每天（末位保留周日）
        ("7-1", "6,0"),  # 7 与 0 同为周日 → 等价 0-1
        ("7-6", "6,0,1,2,3,4,5"),  # 等价 0-6 = 每天
        ("0,6", "6,5"),  # 枚举值逐个换算
        # 步长写法：不能只换算数字（两边起算点不同），必须展开为显式枚举
        ("*/2", "6,1,3,5"),  # 标准 {0,2,4,6}=周日/周二/周四/周六
        ("*/3", "6,2,5"),  # 标准 {0,3,6}=周日/周三/周六
        ("*/1", "6,0,1,2,3,4,5"),  # 每天
        ("2/2", "1,3,5"),  # 标准 2..6 步长 2 = 周二/周四/周六
        ("1-5/2", "0,2,4"),  # 标准 {1,3,5} = 周一/周三/周五
    ],
)
def test_cron_trigger_weekday_field_conversion(raw_weekday, expected_expr):
    """换算自检：数字/区间/步长一律「先取标准集合再逐项换算」，名称与非法形态原样保留。"""
    trigger = scheduler_module._cron_trigger(  # noqa: SLF001
        f"0 3 * * {raw_weekday}", timezone=resolve_timezone("UTC")
    )
    assert str(trigger.fields[4]) == expected_expr  # fields[4] = day_of_week


@pytest.mark.parametrize(
    "raw_weekday",
    [
        "0-fri",  # 数字与名称混杂（畸形）
        "*/0",  # 步长 0
        "*/8",  # 步长越界（P2：不得被展开成「仅周日」这种合法值）
        "*/99",  # 步长越界
        "*/x",  # 步长非数字
        "8",  # 数字越界
        "5-1",  # 环绕区间（标准 cron 亦不支持，两边一致报错）
        "0-8",  # 区间端点越界
    ],
)
def test_cron_trigger_weekday_invalid_passthrough_raises(raw_weekday):
    """非法/无法识别的星期写法**原样透传**给 APScheduler → ``ValueError``。

    关键：不得静默取模把非法值变成合法星期（``8`` 取模会变成周一、``*/0`` 会被
    当成每天、``*/8`` 展开后会变成「仅周日」）——这类"静默把非法值变合法值"正是
    本缺陷的定级依据，故必须由 APScheduler 报错并走「退化仅手动 + WARNING」的兜底。
    """
    with pytest.raises(ValueError):
        scheduler_module._cron_trigger(  # noqa: SLF001
            f"0 3 * * {raw_weekday}", timezone=resolve_timezone("UTC")
        )


@pytest.mark.parametrize("tz", TZ_CASES)
@pytest.mark.parametrize(
    ("cron", "expected_weekdays"),
    [
        ("0 3 * * */2", {6, 1, 3, 5}),  # 标准 */2 = 周日、周二、周四、周六
        ("0 3 * * */3", {6, 2, 5}),  # 标准 */3 = 周日、周三、周六
        ("0 3 * * *", {0, 1, 2, 3, 4, 5, 6}),  # 每天
        ("0 3 * * 1-5", {0, 1, 2, 3, 4}),  # 工作日
        ("0 3 * * mon-fri", {0, 1, 2, 3, 4}),  # 名称写法同样落在工作日
        ("0 3 * * 0,6", {5, 6}),  # 周六、周日
    ],
)
async def test_cron_weekday_fire_set_matches_standard_crontab(
    tmp_path, monkeypatch, tz, cron, expected_weekdays
):
    """**触发星期集合**必须与标准 crontab 一致（不只断言换算后的表达式字符串）。

    步长写法是本缺陷最隐蔽的一面：APScheduler 的 ``*/N`` 从它自己的 0（周一）起算，
    标准 crontab 从 0（周日）起算——两者不是"平移一天"而是**完全不同的集合**
    （``*/2``：标准 {周日,周二,周四,周六} vs APScheduler 原生 {周一,周三,周五,周日}，
    只在周日相交）。故「保留 ``*/2`` 原样、只是锚点差一天」的判断不成立，必须展开枚举
    （APScheduler 3.x 不支持 ``6/2`` 带起点的步长，实测报 "the step value ... is
    higher than the total range"，展开是唯一通路）。

    断言口径：连续取 7 个触发点，换算回任务时区后映射为星期序号（Monday=0 … Sunday=6），
    断言集合相等——与 team-lead 取证表同口径。
    """
    db, scheduler, task_repo, _, _, _ = await make_scheduler(tmp_path, monkeypatch, tz=tz)
    task = await create_task(task_repo, "星期集合", cron=cron)
    await scheduler.reload()

    trigger = scheduler._build_trigger(task.id, cron)  # noqa: SLF001
    assert trigger is not None
    assert set(_next_fire_weekdays(trigger, tz, count=7)) == expected_weekdays
    await db.close()


def _fire_weekdays_within_week(trigger, tz_name: str, week_start: datetime) -> set[int]:
    """取 ``[week_start, week_start+7d)`` 内**全部**触发点映射为星期序号集合。

    与 QA 复验同口径：起点取**周一 00:00（任务时区）**，一周窗口内每天至多命中一次，
    集合即该 cron 的真实星期集合——不受"取 N 次 fire time"时起始星期的影响。
    """
    zone = resolve_timezone(tz_name)
    end = week_start + timedelta(days=7)
    weekdays: set[int] = set()
    previous = None
    while True:
        moment = trigger.get_next_fire_time(previous, week_start if previous is None else previous)
        if moment is None or moment >= end:
            break
        weekdays.add(moment.astimezone(zone).weekday())
        previous = moment
    return weekdays


@pytest.mark.parametrize("tz", TZ_CASES)
@pytest.mark.parametrize(
    ("cron", "expected_weekdays"),
    [
        # P1 回归面：跨周日边界的数字区间（端点独立取模会撞 APScheduler first>last）
        ("0 3 * * 0-1", {6, 0}),  # 周日、周一
        ("0 3 * * 0-2", {6, 0, 1}),  # 周日至周二
        ("0 3 * * 0-3", {6, 0, 1, 2}),  # 周日至周三
        ("0 3 * * 0-4", {6, 0, 1, 2, 3}),  # 周日至周四
        ("0 3 * * 0-5", {6, 0, 1, 2, 3, 4}),  # 周日至周五
        ("0 3 * * 0-6", {0, 1, 2, 3, 4, 5, 6}),  # 周日至周六 = 每天
        ("0 3 * * 7-1", {6, 0}),  # 7≡0：等价 0-1
        ("0 3 * * 7-2", {6, 0, 1}),
        ("0 3 * * 7-3", {6, 0, 1, 2}),
        ("0 3 * * 7-4", {6, 0, 1, 2, 3}),
        ("0 3 * * 7-5", {6, 0, 1, 2, 3, 4}),
        ("0 3 * * 7-6", {0, 1, 2, 3, 4, 5, 6}),  # 等价 0-6 = 每天
        # 同义/相邻写法（原已正常，防回归）
        ("0 3 * * 1-7", {0, 1, 2, 3, 4, 5, 6}),  # 周一至周日 = 每天
        ("0 3 * * 1-5", {0, 1, 2, 3, 4}),  # 工作日
        ("0 3 * * 5-6", {4, 5}),  # 周五、周六
        ("0 3 * * 0-0", {6}),  # 仅周日
        ("0 3 * * 6-6", {5}),  # 仅周六
        ("0 3 * * 0,6", {5, 6}),  # 周末两天
    ],
)
async def test_cron_weekday_range_fire_set_matches_standard_crontab(
    tmp_path, monkeypatch, tz, cron, expected_weekdays
):
    """数字区间 `0-N` / `7-N` 的**触发星期集合**必须与标准 crontab 一致（P1 回归护栏）。

    回归背景：区间两端**独立取模**时 ``0-6`` → ``6-5``（首值 > 末值）撞 APScheduler
    ``triggers/cron/expressions.py`` 的 ``first > last`` 校验抛 ``ValueError`` →
    ``_build_trigger`` 返回 ``None`` → 任务退化「仅手动」。方向虽是 fail-safe，但
    「任务永不自动触发」＝备份静默停止，与原始缺陷同样致命。修法与步长同构：
    先展开标准星期集合、再逐项换算（``7`` 作为起点等价 ``0``，作为终点保留为末位周日）。
    """
    db, scheduler, task_repo, _, _, _ = await make_scheduler(tmp_path, monkeypatch, tz=tz)
    task = await create_task(task_repo, "区间集合", cron=cron)
    await scheduler.reload()

    trigger = scheduler._build_trigger(task.id, cron)  # noqa: SLF001
    assert trigger is not None
    zone = resolve_timezone(tz)
    week_start = datetime(2026, 10, 19, tzinfo=zone)  # 2026-10-19 是周一
    assert _fire_weekdays_within_week(trigger, tz, week_start) == expected_weekdays
    await db.close()


async def test_cron_weekday_out_of_range_degrades_to_manual_only(tmp_path, caplog):
    """星期字段越界（``8``）不换算、原样交给 APScheduler 报错 → WARNING + 退化「仅手动」。

    ``(8 + 6) % 7 = 0`` 会把非法值静默变成周一，故越界数字必须原样透传由 APScheduler
    抛 ``ValueError``，走既有兜底路径（记 WARNING、不建触发器）。
    """
    db, scheduler, task_repo, _, _, _ = await make_scheduler(tmp_path)
    task = await create_task(task_repo, "越界星期", cron="0 3 * * 8")

    with caplog.at_level(logging.WARNING, logger="app.services.scheduler"):
        await scheduler.reload()
        nxt = scheduler.next_fire_time(task)

    assert nxt is None
    assert _backup_job_ids(scheduler) == set()
    assert "无法解析" in caplog.text
    await db.close()


async def test_cron_trigger_rejects_non_five_field_expression():
    """字段数校验（沿用 ``from_crontab`` 口径）：非 5 字段直接 ``ValueError``。"""
    with pytest.raises(ValueError):
        scheduler_module._cron_trigger("0 3 *", timezone=resolve_timezone("UTC"))  # noqa: SLF001
    with pytest.raises(ValueError):
        scheduler_module._cron_trigger("0 3 * * * *", timezone=resolve_timezone("UTC"))  # noqa: SLF001


# ---------------------------------------------------------------- 6. start / shutdown 生命周期


async def test_start_ordering_scheduler_start_then_reload_then_catchup(tmp_path):
    """start 时序不变量：start() → reload() → catchup_on_startup() → mount_maintenance_jobs()。"""
    db, scheduler, _, _, _, _ = await make_scheduler(tmp_path)
    order: list[str] = []

    original_aps_start = scheduler._scheduler.start  # noqa: SLF001

    def recording_aps_start():
        order.append("aps_start")
        return original_aps_start()

    scheduler._scheduler.start = recording_aps_start  # noqa: SLF001 - 实例级替身

    async def fake_reload():
        order.append("reload")

    async def fake_catchup():
        order.append("catchup")

    async def fake_mount():
        order.append("mount_maintenance")

    scheduler.reload = fake_reload  # type: ignore[method-assign]
    scheduler.catchup_on_startup = fake_catchup  # type: ignore[method-assign]
    scheduler.mount_maintenance_jobs = fake_mount  # type: ignore[method-assign]

    await scheduler.start()

    assert order == ["aps_start", "reload", "catchup", "mount_maintenance"]
    assert scheduler._scheduler.running is True  # noqa: SLF001

    # 收尾：停掉真实启动的调度器（shutdown 经 call_soon_threadsafe 派发，需让出一轮）
    await scheduler.shutdown()
    await asyncio.sleep(0)
    assert scheduler._scheduler.running is False  # noqa: SLF001
    await db.close()


async def test_start_integration_catchup_then_shutdown_stops_scheduler(tmp_path, monkeypatch):
    """集成：start 在 recover 之后跑真实 reload+catchup（补跑落库）；shutdown 后调度器停止。"""
    db, scheduler, task_repo, job_repo, _, runner = await make_scheduler(tmp_path, lines=BACKUP_LINES)
    task = await create_task(task_repo, "启动补跑集成", cron="0 3 * * *")
    monkeypatch.setattr(scheduler_module, "datetime", _make_fixed_datetime(FIXED_NOW))

    await scheduler.start()

    jobs, total = await job_repo.list_page(task_id=task.id)
    assert total == 1
    assert jobs[0].trigger == "scheduled"  # 补跑由 catchup 提交
    assert scheduler._scheduler.running is True  # noqa: SLF001

    # shutdown 先停调度：调度器主循环停止（wakeup 定时器取消）→ 不可能再触发新任务——
    # 与 main.py「先 scheduler.shutdown() 再 job_runner.on_shutdown()」时序配套。
    # 注意①：AsyncIOScheduler.shutdown 经 call_soon_threadsafe 派发，需让出一轮事件循环
    # 才真正生效（生产 lifespan 中紧随其后的 on_shutdown awaits 即让出）；
    # 注意②：STOPPED 态 add_job 不抛错、只进 APScheduler pending 列表（start 时自愈），
    # 故不断言"reload 报错"，以 running 标志为准。
    await scheduler.shutdown()
    await asyncio.sleep(0)
    assert scheduler._scheduler.running is False  # noqa: SLF001

    await await_job(runner, jobs[0].id)
    await db.close()


# ---------------------------------------------------------------- 工具


def _make_fixed_datetime(moment: datetime) -> type[datetime]:
    """构造把 ``now()`` 冻结到 ``moment`` 的 datetime 子类（补跑判定确定性）。"""

    class _FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: ANN001, ANN202
            return moment.astimezone(tz) if tz else moment

    return _FixedDatetime


# ---------------------------------------------------------------- 8. 维护任务域（T01 R-020，maintenance-* 分域）


async def test_start_mounts_maintenance_retention_and_reload_preserves_it(tmp_path, monkeypatch):
    """start 挂载 maintenance-retention；reload 动态重载**不得**误删 maintenance-* 触发器。

    结构不变量（F10/设计 §3.5）：reload 的 keep 集合只识别 backup-task- 前缀，
    两域按前缀分域互不清理——这是维护任务挂载点的前置安全条件。
    """
    db, scheduler, task_repo, _, _, _ = await make_scheduler(tmp_path)
    # 冻结 now + 当日点未到的 cron，避免 start 的 catchup 分支提交 backup 干扰断言
    task = await create_task(task_repo, "维护共存", cron="59 23 * * *")
    monkeypatch.setattr(scheduler_module, "datetime", _make_fixed_datetime(FIXED_NOW))

    await scheduler.start()

    assert "maintenance-retention" in _job_ids(scheduler)
    assert f"backup-task-{task.id}" in _job_ids(scheduler)

    await scheduler.reload()  # 动态重载（保存任务后由路由调用）
    assert "maintenance-retention" in _job_ids(scheduler)
    assert f"backup-task-{task.id}" in _job_ids(scheduler)

    await scheduler.shutdown()
    await asyncio.sleep(0)
    await db.close()


async def test_mount_maintenance_jobs_is_idempotent(tmp_path):
    """重复挂载（start + 显式再调）→ replace_existing 去重，不堆积。

    批次 B 面：retention（R-020）与 dump-cleanup（R-019）恒挂载；check 域
    （behavior.check_enabled 缺省 off）不挂载——挂载/摘除的开关矩阵见本节后续用例。
    """
    db, scheduler, _, _, _, _ = await make_scheduler(tmp_path)
    await scheduler.start()
    await scheduler.mount_maintenance_jobs()

    maintenance = [
        job for job in scheduler._scheduler.get_jobs() if str(job.id).startswith("maintenance-")
    ]
    # 只断言集合（排序后清单）：get_jobs 的顺序是 MemoryJobStore 按 next_run_time
    # 排序的实现细节——两个 cron（03:40 / 03:50 UTC）在每天 03:41–03:50 UTC 之间会因
    # retention 翻到明天而互换先后；断言固定顺序 = 每天十分钟窗口内必挂的时间依赖
    # flaky（2026-10-17 实测复现：UTC 03:41 跑全套即挂）。本用例的意图是「恒挂载且
    # 不堆积」，集合等价即覆盖。
    assert sorted(str(job.id) for job in maintenance) == [
        "maintenance-dump-cleanup",
        "maintenance-retention",
    ]

    await scheduler.shutdown()
    await asyncio.sleep(0)
    await db.close()


async def test_fire_retention_invokes_retention_service(tmp_path):
    """维护回调触发 retention 清理（每天到点执行一次 cleanup_once）。"""
    db, scheduler, _, _, _, _ = await make_scheduler(tmp_path)
    calls: list[int] = []

    async def recording_cleanup(*, now=None):  # noqa: ANN001, ANN202
        calls.append(1)
        from app.services.retention import RetentionReport

        return RetentionReport(jobs_deleted=0, log_files_deleted=0, log_bytes_freed=0,
                               jobs_days=90, logs_days=90)

    scheduler._retention.cleanup_once = recording_cleanup  # noqa: SLF001 - 实例级替身

    await scheduler._fire_retention()  # noqa: SLF001

    assert len(calls) == 1
    await db.close()


async def test_fire_retention_isolates_exceptions(tmp_path, caplog):
    """清理异常被隔离记日志，不向调度循环上抛（援 _submit 单任务隔离口径）。"""
    db, scheduler, _, _, _, _ = await make_scheduler(tmp_path)

    async def failing_cleanup(*, now=None):  # noqa: ANN001, ANN202
        raise RuntimeError("cleanup boom")

    scheduler._retention.cleanup_once = failing_cleanup  # noqa: SLF001 - 实例级替身

    with caplog.at_level(logging.ERROR, logger="app.services.scheduler"):
        await scheduler._fire_retention()  # 不得抛异常  # noqa: SLF001

    assert "retention 清理失败" in caplog.text
    await db.close()


# ---------------------------------------------------------------- 9. check 维护域（T04 R-017，批次 B）

# 维护域 job id（与实现同源；挂载/摘除断言的口径）
CHECK_SAMPLE_ID = "maintenance-check-sample"
CHECK_FULL_ID = "maintenance-check-full"
DUMP_CLEANUP_ID = "maintenance-dump-cleanup"


def _maintenance_job_ids(scheduler: SchedulerService) -> set[str]:
    """当前调度器内 ``maintenance-`` 域触发器 id 集合。"""
    return {  # noqa: SLF001
        str(job.id) for job in scheduler._scheduler.get_jobs() if str(job.id).startswith("maintenance-")
    }


async def test_check_jobs_mounted_when_enabled(tmp_path):
    """check_enabled=1 → 每日 n/t 扫描（t>0）挂载；月度全量需独立开关 =1 才挂载。"""
    db, scheduler, _, _, config_repo, _ = await make_scheduler(tmp_path)
    await scheduler.start()
    assert CHECK_SAMPLE_ID not in _maintenance_job_ids(scheduler)  # 缺省 off

    await config_repo.set(BEHAVIOR_CHECK_ENABLED_KEY, "1")
    await config_repo.set(BEHAVIOR_CHECK_SUBSET_DENOMINATOR_KEY, "30")
    await config_repo.set(BEHAVIOR_MONTHLY_FULL_DAY_KEY, "15")
    await config_repo.set(BEHAVIOR_MONTHLY_FULL_ENABLED_KEY, "1")
    await scheduler.reload()  # 保存即生效（reload 末尾补挂维护域）

    assert {CHECK_SAMPLE_ID, CHECK_FULL_ID} <= _maintenance_job_ids(scheduler)
    # 月度全量的触发日 = monthly_full_day（当月 15 日 04:00）
    full = next(j for j in scheduler._scheduler.get_jobs() if str(j.id) == CHECK_FULL_ID)  # noqa: SLF001
    nxt = full.trigger.get_next_fire_time(None, datetime.now(UTC))
    assert nxt is not None and nxt.day == 15 and nxt.hour == 4

    # 开关关闭 → 触发器摘除（同一前缀分域机制）
    await config_repo.set(BEHAVIOR_CHECK_ENABLED_KEY, "0")
    await scheduler.reload()
    assert CHECK_SAMPLE_ID not in _maintenance_job_ids(scheduler)
    assert CHECK_FULL_ID not in _maintenance_job_ids(scheduler)

    await scheduler.shutdown()
    await asyncio.sleep(0)
    await db.close()


async def test_check_sample_not_mounted_when_denominator_zero(tmp_path):
    """check_subset_denominator=0 → 不做每日扫描（只月度全量），sample 触发器不挂载。"""
    db, scheduler, _, _, config_repo, _ = await make_scheduler(tmp_path)
    await config_repo.set(BEHAVIOR_CHECK_ENABLED_KEY, "1")
    await config_repo.set(BEHAVIOR_CHECK_SUBSET_DENOMINATOR_KEY, "0")
    await config_repo.set(BEHAVIOR_MONTHLY_FULL_ENABLED_KEY, "1")

    await scheduler.start()

    assert CHECK_SAMPLE_ID not in _maintenance_job_ids(scheduler)
    assert CHECK_FULL_ID in _maintenance_job_ids(scheduler)

    await scheduler.shutdown()
    await asyncio.sleep(0)
    await db.close()


async def test_monthly_full_not_mounted_by_default(tmp_path):
    """月度全量独立开关缺省关：即便配了 monthly_full_day，也不挂载（方案 A 默认态）。"""
    db, scheduler, _, _, config_repo, _ = await make_scheduler(tmp_path)
    await config_repo.set(BEHAVIOR_CHECK_ENABLED_KEY, "1")
    await config_repo.set(BEHAVIOR_CHECK_SUBSET_DENOMINATOR_KEY, "30")
    await config_repo.set(BEHAVIOR_MONTHLY_FULL_DAY_KEY, "15")
    # 故意不设 BEHAVIOR_MONTHLY_FULL_ENABLED_KEY

    await scheduler.start()

    assert CHECK_SAMPLE_ID in _maintenance_job_ids(scheduler)
    assert CHECK_FULL_ID not in _maintenance_job_ids(scheduler)

    # 打开独立开关 → 恢复挂载（方案 B）
    await config_repo.set(BEHAVIOR_MONTHLY_FULL_ENABLED_KEY, "1")
    await scheduler.reload()
    assert CHECK_FULL_ID in _maintenance_job_ids(scheduler)

    await scheduler.shutdown()
    await asyncio.sleep(0)
    await db.close()


async def test_monthly_full_day_invalid_skips_full_job(tmp_path):
    """monthly_full_day 越界（31 > 28）→ 月度全量不挂载（WARNING），抽样照常，不崩溃。"""
    db, scheduler, _, _, config_repo, _ = await make_scheduler(tmp_path)
    await config_repo.set(BEHAVIOR_CHECK_ENABLED_KEY, "1")
    await config_repo.set(BEHAVIOR_MONTHLY_FULL_DAY_KEY, "31")

    await scheduler.start()  # 不得抛异常

    assert CHECK_SAMPLE_ID in _maintenance_job_ids(scheduler)
    assert CHECK_FULL_ID not in _maintenance_job_ids(scheduler)

    await scheduler.shutdown()
    await asyncio.sleep(0)
    await db.close()


async def test_fire_check_sample_submits_with_subset(tmp_path, monkeypatch):
    """抽样回调：提交 scheduled check，params 带 read_data=False + n/t 形态 subset。

    n/t 为确定性分桶（替代旧的随机 ``x%``）：t = 周期天数，n 由当天日期序数推出，
    值域 [1, t]。完整「一轮覆盖全部分桶」的性质见
    ``tests/test_scheduler_check_multirepo.py::test_sample_bucket_covers_all_buckets``。
    """
    db, scheduler, _, _, config_repo, runner = await make_scheduler(tmp_path)
    await config_repo.set(BEHAVIOR_CHECK_ENABLED_KEY, "1")
    await config_repo.set(BEHAVIOR_CHECK_SUBSET_DENOMINATOR_KEY, "30")
    # 桩成已就绪，聚焦「多仓库提交」语义（跳过逻辑见 test_fire_check_skips_unconfigured_repo）
    monkeypatch.setattr("app.services.scheduler.passfile_status", lambda *a, **k: "configured")

    await scheduler._fire_check("sample")  # noqa: SLF001

    jobs, total = await runner._jobs.list_page(job_type="check")  # noqa: SLF001
    assert total == 1
    assert jobs[0].trigger == "scheduled"
    params = runner._params[jobs[0].id]  # noqa: SLF001
    assert params["read_data"] is False
    match = re.fullmatch(r"(\d+)/30", str(params["read_data_subset"]))
    assert match is not None, params
    assert 1 <= int(match.group(1)) <= 30


async def test_fire_check_full_submits_read_data(tmp_path, monkeypatch):
    """全量回调：params 带 read_data=True、无 subset（互斥语义由白名单层兜底）。

    月度全量受独立开关 ``behavior.monthly_full_enabled`` 门控（缺省关）。
    """
    db, scheduler, _, _, config_repo, runner = await make_scheduler(tmp_path)
    await config_repo.set(BEHAVIOR_CHECK_ENABLED_KEY, "1")
    await config_repo.set(BEHAVIOR_MONTHLY_FULL_ENABLED_KEY, "1")
    monkeypatch.setattr("app.services.scheduler.passfile_status", lambda *a, **k: "configured")

    await scheduler._fire_check("full")  # noqa: SLF001

    jobs, total = await runner._jobs.list_page(job_type="check")  # noqa: SLF001
    assert total == 1
    assert runner._params[jobs[0].id] == {"read_data": True}  # noqa: SLF001


async def test_fire_check_skips_when_disabled_or_denominator_zero(tmp_path, monkeypatch):
    """t=0 / 月度全量开关关 → fire 均不提交（覆盖 reload 摘除触发器前的窗口）。"""
    db, scheduler, _, _, config_repo, runner = await make_scheduler(tmp_path)
    await config_repo.set(BEHAVIOR_CHECK_ENABLED_KEY, "1")
    await config_repo.set(BEHAVIOR_CHECK_SUBSET_DENOMINATOR_KEY, "0")
    monkeypatch.setattr("app.services.scheduler.passfile_status", lambda *a, **k: "configured")

    await scheduler._fire_check("sample")  # noqa: SLF001 - t=0 = 不做每日扫描
    await scheduler._fire_check("full")  # noqa: SLF001 - 月度全量开关缺省关

    assert (await runner._jobs.list_page(job_type="check"))[1] == 0  # noqa: SLF001

    await config_repo.set(BEHAVIOR_CHECK_ENABLED_KEY, "0")  # 总开关关闭同样静默
    await scheduler._fire_check("sample")  # noqa: SLF001
    assert (await runner._jobs.list_page(job_type="check"))[1] == 0  # noqa: SLF001
    await db.close()


async def test_fire_check_isolates_exceptions(tmp_path, caplog, monkeypatch):
    """提交异常被隔离记日志，不向调度循环上抛（援 _submit/_fire_retention 口径）。"""
    db, scheduler, _, _, config_repo, runner = await make_scheduler(tmp_path)
    await config_repo.set(BEHAVIOR_CHECK_ENABLED_KEY, "1")
    monkeypatch.setattr("app.services.scheduler.passfile_status", lambda *a, **k: "configured")

    async def failing_submit(*args, **kwargs):  # noqa: ANN001, ANN002, ANN202
        raise RuntimeError("submit boom")

    runner.submit = failing_submit  # type: ignore[method-assign]
    with caplog.at_level(logging.ERROR, logger="app.services.scheduler"):
        await scheduler._fire_check("sample")  # 不得抛异常  # noqa: SLF001

    assert "调度提交 check 失败" in caplog.text
    await db.close()


# ---------------------------------------------------------------- 10. dump 产物清理维护域（T05 R-019，批次 B）


async def test_fire_dump_cleanup_purges_expired_files(tmp_path):
    """清理兜底：dump_dir 内超期产物删除、保留期内文件与目录外的文件原样保留。"""
    db, scheduler, _, _, config_repo, _ = await make_scheduler(tmp_path)
    dump_dir = scheduler._settings.dump_dir  # noqa: SLF001
    dump_dir.mkdir(parents=True, exist_ok=True)
    stale = dump_dir / "appdb-job-old-20260101T000000Z.sql"
    stale.write_bytes(b"stale")
    fresh = dump_dir / "appdb-job-new-20990101T000000Z.sql"
    fresh.write_bytes(b"fresh")
    os.utime(stale, (1_000_000_000, 1_000_000_000))  # 2001 年 → 远超 7 天保留
    outside = scheduler._settings.data_dir + "/outside.sql"  # noqa: SLF001
    Path(outside).write_bytes(b"keep")

    await scheduler._fire_dump_cleanup()  # noqa: SLF001

    assert not stale.exists()
    assert fresh.exists()
    assert Path(outside).exists()
    await db.close()


async def test_fire_dump_cleanup_noop_when_retention_zero(tmp_path):
    """retention_days=0 = 不启用（fail-safe）：文件原样保留。"""
    db, scheduler, _, _, config_repo, _ = await make_scheduler(tmp_path)
    await config_repo.set(DUMP_RETENTION_DAYS_KEY, "0")
    dump_dir = scheduler._settings.dump_dir  # noqa: SLF001
    dump_dir.mkdir(parents=True, exist_ok=True)
    keep = dump_dir / "old.sql"
    keep.write_bytes(b"keep")

    await scheduler._fire_dump_cleanup()  # noqa: SLF001

    assert keep.exists()
    await db.close()
