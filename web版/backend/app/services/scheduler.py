"""调度器（R-013 从零落地，增量设计 §4）：per-task cron 触发、动态重载、启动补跑。

最小可用面全量实现（APScheduler AsyncIOScheduler，架构 §2.1 既选型本批首次启用）：
- 每个 ``enabled=1`` 且有效 cron 非空的任务一个 ``CronTrigger``（job id
  ``backup-task-<id>``，``replace_existing``）；保存/删除后 :meth:`reload` 全量重建；
- 有效 cron（单一实现点 :meth:`_effective_cron`）：``task.cron`` → config
  ``schedule.cron_default`` → 无（仅手动）；时区 ``config.timezone`` → ``settings.tz`` → UTC；
- jitter 在 fire 回调内 sleep（顺序：查 enabled → jitter → 记账 → submit），
  先记账后执行防崩溃重复触发（§6.4）；
- 补跑（:meth:`catchup_on_startup`）：当天首个应触发点已过且无成功 backup → 补跑一次；
- **prune 边界（N3 裁决）**：:meth:`_due_for_prune` 只判定并在 E34 卡片暴露
  ``prune_due``，本批**不自动提交 forget**（R-016 预览-确认流不在本增量池内）。

线程/并发模型：单进程单事件循环；提交仍全量走 :class:`JobRunner` 的仓库维度互斥
（E1 不回归——调度器自身不并行设计，冲突落 cancelled + 审计后下个周期自然再试）。
"""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import UTC, datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.db.repositories.config_repo import ConfigRepo
from app.db.repositories.job_repo import JobRepo
from app.db.repositories.task_repo import DEFAULT_REPO_ID, Task, TaskRepo
from app.services.job_runner import JobRunner
from app.settings import Settings, get_settings
from app.utils.time import parse_iso, resolve_timezone, utc_iso

logger = logging.getLogger(__name__)

# 全局调度缺省键（config 表；N5：本批无 UI，键缺失时按下述常量兜底）
SCHEDULE_CRON_DEFAULT_KEY = "schedule.cron_default"
SCHEDULE_JITTER_KEY = "schedule.jitter"
TIMEZONE_KEY = "timezone"

# 可调阈值（常量只作默认值单一真源；实际值走 config，见 AGENTS.md「可调阈值」表）
SCHEDULE_JITTER_DEFAULT_SECONDS = 300   # 抖动缺省（秒；架构 §6.8「jitter 0–300s」）
SCHEDULE_JITTER_MAX_SECONDS = 3600      # 抖动上限防护（配置被写爆时兜底，防失控长睡）

# 调度 job id 前缀（reload 全量重建时据此识别「属于本调度器的触发器」）
_JOB_ID_PREFIX = "backup-task-"


class SchedulerService:
    """per-task 调度器（增量设计 §4.1 签名全量）。"""

    def __init__(
        self,
        tasks: TaskRepo,
        runner: JobRunner,
        config: ConfigRepo,
        settings: Settings | None = None,
        *,
        jobs: JobRepo,
    ) -> None:
        """构造调度器。

        Args:
            tasks: 任务仓储（触发器列举与任务复核）。
            runner: 任务执行器（submit 走仓库维度互斥，E1）。
            config: 配置仓储（cron_default/jitter/timezone 全局缺省）。
            settings: 运行期配置；缺省取进程单例（时区回退源）。
            jobs: jobs 表仓储（补跑/prune 判定需要读 backup 历史）。
                设计 §4.1 构造签名未列此项，但 catchup/prune 判定必须读 jobs 表，
                且 A1 要求 DB 访问只经仓储——JobRunner 未暴露该查询，故显式注入。
        """
        self._tasks = tasks
        self._runner = runner
        self._config = config
        self._settings = settings or get_settings()
        self._jobs = jobs
        self._scheduler = AsyncIOScheduler()
        # config 快照（next_fire_time 为同步签名，无法直接 await config 读取；
        # 由 reload()/catchup_on_startup() 入口刷新——cron_default/timezone 本批无 UI）
        self._cron_default: str | None = None
        self._tz = resolve_timezone(self._settings.tz)

    # ---------------------------------------------------------------- 生命周期（§4.3 接线点）

    async def start(self) -> None:
        """``AsyncIOScheduler.start()`` → :meth:`reload` → :meth:`catchup_on_startup`（lifespan 接线）。"""
        self._scheduler.start()
        await self.reload()
        await self.catchup_on_startup()

    async def shutdown(self) -> None:
        """``scheduler.shutdown(wait=True)``——与 ``JobRunner.on_shutdown`` 顺序：先停调度再收尾进程。"""
        self._scheduler.shutdown(wait=True)

    # ---------------------------------------------------------------- 动态重载（US-026-02 AC1）

    async def reload(self) -> None:
        """全量重建 backup 触发器（保存/删除后由路由调用，「保存即生效不重启容器」）。

        ``enabled=1`` 且有效 cron 非空的任务各建一个 ``CronTrigger.from_crontab``
        （``replace_existing``）；随后移除已不存在/已暂停/无有效 cron 任务的遗留 job。
        """
        await self._refresh_config_snapshot()
        tasks = await self._tasks.list_all(DEFAULT_REPO_ID)
        keep: set[str] = set()
        for task in tasks:
            job_id = self._job_id(task.id)
            if not task.enabled:
                continue
            cron = self._effective_cron(task)
            if not cron:
                continue
            trigger = self._build_trigger(task.id, cron)
            if trigger is None:
                continue
            self._scheduler.add_job(
                self._fire_backup,
                trigger=trigger,
                args=(task.id,),
                id=job_id,
                replace_existing=True,
            )
            keep.add(job_id)

        for job in self._scheduler.get_jobs():
            if job.id.startswith(_JOB_ID_PREFIX) and job.id not in keep:
                logger.info("调度器移除遗留触发器：%s", job.id)
                self._scheduler.remove_job(job.id)

    # ---------------------------------------------------------------- 启动补跑（US-026-07 AC3）

    async def catchup_on_startup(self) -> None:
        """补跑：逐任务算「今天首个应触发点」，已过点且无成功 backup → 补跑一次。

        判据（N8 裁决）：``trigger.get_next_fire_time(None, 当天 00:00(tz))`` ≤ now
        且该任务无 ``finished_at ≥ 该点`` 的成功 backup job。补跑提交若遇运行中任务，
        走 JobRunner scheduled 冲突分支（cancelled + ``job.skipped_conflict`` 审计），
        不排队，下个周期自然再试（E1）。
        """
        await self._refresh_config_snapshot()
        now = datetime.now(UTC)
        today_start = now.astimezone(self._tz).replace(hour=0, minute=0, second=0, microsecond=0)

        for task in await self._tasks.list_all(DEFAULT_REPO_ID):
            if not task.enabled:
                continue
            cron = self._effective_cron(task)
            if not cron:
                continue
            trigger = self._build_trigger(task.id, cron)
            if trigger is None:
                continue
            first_today = trigger.get_next_fire_time(None, today_start)
            logger.warning(  # TODO(CI-DIAG): 临时诊断，定位后删
                "DIAGC2 task=%s cmp_continue=%r first_today=%r now=%r tz=%r trigger_tz=%r cron=%r",
                task.id,
                (first_today is None or first_today > now),
                first_today,
                now,
                self._tz,
                trigger.timezone,
                cron,
            )
            if first_today is None or first_today > now:
                continue  # 今天的应触发点未到（或当天不触发）
            last_success = await self._jobs.last_backup_success_finished_at(task.id)
            if last_success is not None:
                finished = parse_iso(last_success)
                if finished is not None and finished >= first_today:
                    continue  # 当天已成功过，无需补跑
            await self._submit(task, reason="catchup")

    # ---------------------------------------------------------------- E34 卡片推导

    def next_fire_time(self, task: Task) -> str | None:
        """E34 卡片「下次运行」：有效 cron 的 ``get_next_fire_time(None, now)`` → ISO 8601 UTC。

        ``enabled=0`` 或无有效 cron（含无法解析）返回 None（UI：已暂停 / 仅手动）。
        """
        if not task.enabled:
            return None
        cron = self._effective_cron(task)
        if not cron:
            return None
        trigger = self._build_trigger(task.id, cron)
        if trigger is None:
            return None
        nxt = trigger.get_next_fire_time(None, datetime.now(UTC))
        return utc_iso(nxt) if nxt is not None else None

    async def _due_for_prune(self, task: Task) -> bool:
        """prune 到期独立判定（§6.4 / N3）：距上次成功 backup 超 ``prune_interval_days``。

        **只判定，不执行**——到期仅在 E34 卡片暴露 ``prune_due`` 提示；自动 forget
        留待 R-016 预览-确认流（G3：不发生未经确认门的裁剪事故）。
        ``prune_interval_days <= 0`` 视为不启用（与 ``limit_*=0`` 不限同口径）；
        从未成功备份 → 无可清理对象 → False。
        """
        if task.prune_interval_days <= 0:
            return False
        last_success = await self._jobs.last_backup_success_finished_at(task.id)
        finished = parse_iso(last_success) if last_success else None
        if finished is None:
            return False
        return datetime.now(UTC) - finished > timedelta(days=task.prune_interval_days)

    # ---------------------------------------------------------------- 触发链（§4.1）

    async def _fire_backup(self, task_id: int) -> None:
        """触发链：读任务 → enabled 复核 → sleep(jitter) → 记账 → submit。

        先记账后执行（防崩溃重复触发）；容器在 jitter 窗口内死亡则无记账，
        补跑逻辑自然覆盖。jitter 在回调内 sleep（非 trigger jitter）——顺序即设计口径。
        """
        task = await self._tasks.get(task_id)
        if task is None:
            logger.info("调度触发时任务 %s 已删除，跳过", task_id)
            return
        if not task.enabled:
            logger.info("调度触发时任务 %s 已暂停，跳过（触发器残留由 reload 清理）", task_id)
            return

        jitter = await self._jitter_seconds()
        if jitter > 0:
            await asyncio.sleep(random.uniform(0, jitter))

        # 记账先行：last_run_at = now（防崩溃重复触发，§6.4）
        await self._tasks.set_last_run_at(task_id, utc_iso())
        await self._submit(task, reason="scheduled")

    # ---------------------------------------------------------------- 内部实现

    async def _submit(self, task: Task, *, reason: str) -> None:
        """提交 backup（scheduled）；异常按任务隔离记录，不中断调用方循环。"""
        try:
            await self._runner.submit("backup", task_id=task.id, trigger="scheduled")
        except Exception:  # noqa: BLE001 - 单任务提交失败不应拖垮整个补跑/触发循环
            logger.exception("调度提交失败：task=%s reason=%s", task.id, reason)

    async def _refresh_config_snapshot(self) -> None:
        """刷新 config 快照（cron_default / timezone）——同步方法的数据来源。"""
        raw_cron = await self._config.get_value(SCHEDULE_CRON_DEFAULT_KEY)
        self._cron_default = (raw_cron or "").strip() or None
        tz_name = (await self._config.get_value(TIMEZONE_KEY)) or self._settings.tz
        self._tz = resolve_timezone(tz_name)

    def _effective_cron(self, task: Task) -> str | None:
        """有效 cron 解析（单一实现点）：task.cron → config cron_default → 无（仅手动）。"""
        if task.cron and task.cron.strip():
            return task.cron.strip()
        return self._cron_default

    def _build_trigger(self, task_id: int, cron: str) -> CronTrigger | None:
        """按有效 cron 建触发器；无法解析（如 6 字段秒级表达式）时记 WARNING 并返回 None。"""
        try:
            return CronTrigger.from_crontab(cron, timezone=self._tz)
        except ValueError as exc:
            logger.warning(
                "任务 %s 的 cron %r 无法解析（from_crontab 仅收 5 字段）：%s；该任务不建触发器（仅手动）",
                task_id,
                cron,
                exc,
            )
            return None

    async def _jitter_seconds(self) -> int:
        """读取全局抖动（config ``schedule.jitter``，秒）；非法值回退缺省并告警。"""
        raw = await self._config.get_value(SCHEDULE_JITTER_KEY)
        if raw is None or str(raw).strip() == "":
            return SCHEDULE_JITTER_DEFAULT_SECONDS
        try:
            value = int(str(raw).strip())
        except ValueError:
            logger.warning(
                "schedule.jitter 配置非法：%r，回退缺省 %ss", raw, SCHEDULE_JITTER_DEFAULT_SECONDS
            )
            return SCHEDULE_JITTER_DEFAULT_SECONDS
        return max(0, min(value, SCHEDULE_JITTER_MAX_SECONDS))

    @staticmethod
    def _job_id(task_id: int) -> str:
        """调度 job id（``backup-task-<id>``，replace_existing 语义依赖其稳定性）。"""
        return f"{_JOB_ID_PREFIX}{task_id}"
