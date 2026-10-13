"""SchedulerService 测试（增量设计 §4 / R-013，X3 owner 裁决提前补测）。

覆盖设计 §7-T04 验收口径的六个面：
1. ``reload`` 触发器重建（cron 变更重建、新增/删除同步增减）；
2. ``catchup_on_startup`` 补跑判据（当天已过点且无成功 → 补跑；当天有成功 → 不补跑；
   跨天边界；冲突走 scheduled 分支落 cancelled + ``job.skipped_conflict`` 审计）；
3. jitter 范围与「先记账后执行」顺序；
4. prune 只提示不执行（N3：到期不自动提交 forget）；
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
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from test_state_machine import BACKUP_LINES, FakeRestic, await_job, insert_job

from app.db.database import Database
from app.db.repositories.audit_repo import AuditRepo
from app.db.repositories.config_repo import ConfigRepo
from app.db.repositories.job_repo import JobRepo
from app.db.repositories.task_repo import TaskRepo
from app.services import scheduler as scheduler_module
from app.services.job_runner import JobRunner
from app.services.parser import OutputParser
from app.services.scheduler import (
    SCHEDULE_CRON_DEFAULT_KEY,
    SCHEDULE_JITTER_DEFAULT_SECONDS,
    SCHEDULE_JITTER_KEY,
    SCHEDULE_JITTER_MAX_SECONDS,
    SchedulerService,
)
from app.settings import Settings, get_settings
from app.utils.time import now_utc, parse_iso, utc_iso

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
    runner = JobRunner(
        db,
        FakeRestic(lines=lines, exit_code=exit_code),  # type: ignore[arg-type]
        OutputParser(),
        AuditRepo(db),
        settings=settings,
        task_loader=task_repo.get_dict,
    )
    scheduler = SchedulerService(task_repo, runner, config_repo, settings=settings, jobs=job_repo)
    return db, scheduler, task_repo, job_repo, config_repo, runner


async def create_task(task_repo: TaskRepo, name: str, **overrides) -> object:
    """直写仓储建任务（绕过 API 校验——兜底测试需要模拟运维直写的坏值）。"""
    values: dict = {"name": name, "enabled": 1, "sources": ["/host/srv"], "excludes": []}
    values.update(overrides)
    return await task_repo.create(values)


async def insert_success_job(job_repo: JobRepo, job_id: str, task_id: int, finished_at: str) -> None:
    """落一条成功 backup（带 finished_at）——补跑/prune 判定的历史数据。"""
    await job_repo.create(
        job_id=job_id,
        job_type="backup",
        trigger="scheduled",
        log_path=f"/tmp/{job_id}.log",
        status="success",
        task_id=task_id,
    )
    await job_repo.update_status(job_id, "success", finished_at=finished_at)


def _job_ids(scheduler: SchedulerService) -> set[str]:
    """当前调度器内的触发器 id 集合（``backup-task-<id>``）。"""
    return {str(job.id) for job in scheduler._scheduler.get_jobs()}  # noqa: SLF001


# ---------------------------------------------------------------- 1. reload 触发器重建


async def test_reload_creates_trigger_per_enabled_task(tmp_path):
    """enabled=1 且有效 cron 非空 → 各建一个触发器；无 cron/暂停 → 不建。"""
    db, scheduler, task_repo, _, _, _ = await make_scheduler(tmp_path)
    task_a = await create_task(task_repo, "A", cron="0 3 * * *")
    await create_task(task_repo, "B", cron="0 4 * * *", enabled=0)
    await create_task(task_repo, "C")  # 无 cron → 仅手动

    await scheduler.reload()

    assert _job_ids(scheduler) == {f"backup-task-{task_a.id}"}
    job = scheduler._scheduler.get_jobs()[0]  # noqa: SLF001
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
    first_next = scheduler._scheduler.get_jobs()[0].trigger.get_next_fire_time(None, reference)  # noqa: SLF001
    assert first_next.hour == 18

    await task_repo.update(task.id, {"cron": "0 19 * * *"})
    await scheduler.reload()

    jobs = scheduler._scheduler.get_jobs()  # noqa: SLF001
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
    assert _job_ids(scheduler) == {f"backup-task-{task_a.id}"}

    task_b = await create_task(task_repo, "B", cron="0 4 * * *")
    await scheduler.reload()
    assert _job_ids(scheduler) == {f"backup-task-{task_a.id}", f"backup-task-{task_b.id}"}

    await task_repo.update(task_b.id, {"enabled": 0})
    await scheduler.reload()
    assert _job_ids(scheduler) == {f"backup-task-{task_a.id}"}

    await task_repo.update(task_b.id, {"enabled": 1})
    await scheduler.reload()
    assert _job_ids(scheduler) == {f"backup-task-{task_a.id}", f"backup-task-{task_b.id}"}

    await task_repo.delete(task_b.id)
    await scheduler.reload()
    assert _job_ids(scheduler) == {f"backup-task-{task_a.id}"}
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
    assert first.astimezone(ZoneInfo(tz)).hour == 5
    assert first > now_utc()

    await config_repo.set(SCHEDULE_CRON_DEFAULT_KEY, "0 6 * * *")
    await scheduler.reload()
    second = parse_iso(scheduler.next_fire_time(task))
    assert second is not None
    assert second.astimezone(ZoneInfo(tz)).hour == 6
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
    assert kwargs == {"task_id": task.id, "trigger": "scheduled"}
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


# ---------------------------------------------------------------- 4. prune 只提示（N3）


async def test_due_for_prune_never_submits_forget(tmp_path, monkeypatch):
    """N3：prune 到期只判定提示——catchup 与 fire 链路全程不得提交 forget。"""
    db, scheduler, task_repo, job_repo, config_repo, runner = await make_scheduler(tmp_path)
    task = await create_task(task_repo, "prune 到期", cron="0 3 * * *", prune_interval_days=7)
    # 10 天前的成功 backup → 距上次成功超 7 天 → 到期
    await insert_success_job(job_repo, "job-old-ok", task.id, utc_iso(now_utc() - timedelta(days=10)))
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

    # 两次链路只提交 backup，绝不自动 forget（R-016 预览-确认流不在本批）
    assert [job_type for job_type, _ in submitted] == ["backup", "backup"]
    jobs, _ = await job_repo.list_page()
    assert all(job.type != "forget" for job in jobs)

    for _, job in submitted:  # 收尾后台执行任务，避免跨测试泄漏
        await await_job(runner, job.id)  # type: ignore[attr-defined]
    await db.close()


async def test_due_for_prune_false_when_recent_never_or_zero_interval(tmp_path):
    """未到期（近期成功）/ 从未成功 / 间隔 0（不启用）→ False。"""
    db, scheduler, task_repo, job_repo, _, _ = await make_scheduler(tmp_path)

    recent = await create_task(task_repo, "近期成功", prune_interval_days=7)
    await insert_success_job(job_repo, "job-recent", recent.id, utc_iso(now_utc() - timedelta(days=1)))
    assert await scheduler._due_for_prune(recent) is False

    never = await create_task(task_repo, "从未成功", prune_interval_days=7)
    assert await scheduler._due_for_prune(never) is False

    # prune_interval_days=0 读回归一已由 X3 修复并有 test_repos.py 护栏；
    # 此处直接用仓储读回的行验证 due_for_prune 的 <=0 分支
    zero_row = await create_task(task_repo, "间隔为零", prune_interval_days=0)
    assert zero_row.prune_interval_days == 0
    await insert_success_job(job_repo, "job-zero", zero_row.id, utc_iso(now_utc() - timedelta(days=99)))
    assert await scheduler._due_for_prune(zero_row) is False
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
    assert _job_ids(scheduler) == set()
    assert "无法解析" in caplog.text
    await db.close()


async def test_broken_config_cron_default_degrades_all_to_manual_only(tmp_path, caplog):
    """config schedule.cron_default 被运维直写坏（不经 PolicyService 校验）→ 兜底降级。

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
    assert _job_ids(scheduler) == set()
    assert "无法解析" in caplog.text
    await db.close()


async def test_valid_config_cron_default_still_builds_trigger(tmp_path):
    """对照组：config cron_default 合法 → 无任务级 cron 的任务照常建触发器。"""
    db, scheduler, task_repo, _, config_repo, _ = await make_scheduler(tmp_path)
    await config_repo.set(SCHEDULE_CRON_DEFAULT_KEY, "0 5 * * *")
    task = await create_task(task_repo, "全局好缺省")

    await scheduler.reload()

    assert _job_ids(scheduler) == {f"backup-task-{task.id}"}
    await db.close()


# ---------------------------------------------------------------- 6. start / shutdown 生命周期


async def test_start_ordering_scheduler_start_then_reload_then_catchup(tmp_path):
    """start 时序不变量：AsyncIOScheduler.start() → reload() → catchup_on_startup()。"""
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

    scheduler.reload = fake_reload  # type: ignore[method-assign]
    scheduler.catchup_on_startup = fake_catchup  # type: ignore[method-assign]

    await scheduler.start()

    assert order == ["aps_start", "reload", "catchup"]
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
