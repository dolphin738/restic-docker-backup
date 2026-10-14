"""调度器（R-013 从零落地，增量设计 §4）：per-task cron 触发、动态重载、启动补跑。

最小可用面全量实现（APScheduler AsyncIOScheduler，架构 §2.1 既选型本批首次启用）：
- 每个 ``enabled=1`` 且有效 cron 非空的任务一个 ``CronTrigger``（job id
  ``backup-task-<id>``，``replace_existing``）；保存/删除后 :meth:`reload` 全量重建；
- 有效 cron（单一实现点 :meth:`_effective_cron`）：``task.cron`` → config
  ``schedule.cron_default`` → 无（仅手动）；时区 ``config.timezone`` → ``settings.tz`` → UTC；
- jitter 在 fire 回调内 sleep（顺序：查 enabled → jitter → 记账 → submit），
  先记账后执行防崩溃重复触发（§6.4）；
- 补跑（:meth:`catchup_on_startup`）：当天首个应触发点已过且无成功 backup → 补跑一次；
- **prune 边界（N3 裁决 2026-10-14 开放自动）**：:meth:`_due_for_prune` 判定到期；
  手动裁剪走 R-016 预览-确认流（E21 + 三重确认门），**自动 forget** 由
  :meth:`maybe_auto_forget` 在 backup 成功回调里触发（per-task keep 规则 + prune，
  G3 服务端兜底与审计沿用 JobRunner forget 分支既有语义；结果经 R-018 Notifier
  事后告知——「事后告知」替代「事前确认」的裁决口径）。

线程/并发模型：单进程单事件循环；提交仍全量走 :class:`JobRunner` 的仓库维度互斥
（E1 不回归——调度器自身不并行设计，冲突落 cancelled + 审计后下个周期自然再试）。
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta, tzinfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.db.repositories.config_repo import ConfigRepo
from app.db.repositories.job_repo import JobRepo
from app.db.repositories.task_repo import DEFAULT_REPO_ID, Task, TaskRepo
from app.services.job_runner import JobRunner
from app.services.retention import RetentionService
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

# 维护任务域前缀（设计 §3.5）：reload() 的 keep 集合只识别 backup-task- 前缀，
# maintenance-* 触发器不会被动态重载误删——两域按前缀分域、互不清理（F10 结构不变量）
_MAINTENANCE_PREFIX = "maintenance-"

# 每日维护固定时刻（模块级常量单一真源，设计 §3.5；时区取 config.timezone → settings.tz）
MAINTENANCE_RETENTION_CRON = "40 3 * * *"  # 每日 retention（R-020；check/dump 随批次 B 追加）

# 标准 crontab 星期取值上限（0=周日 … 6=周六，7 与 0 同义）；超出即原样透传由 APScheduler 报错
_CRON_WEEKDAY_MAX = 7
# 标准 crontab 星期取值末位（``a/N`` 未写终点时的缺省终点）
_CRON_WEEKDAY_LAST = 6

# 星期字段内的数字
_WEEKDAY_NUMBER_RE = re.compile(r"\d+")
# 星期字段内的名称（mon/tue/…/sun 及其缩写）：含字母即按名称段处理
_WEEKDAY_NAME_RE = re.compile(r"[A-Za-z]")


def _expand_weekday_values(days: Iterable[int]) -> str:
    """把标准 crontab 的星期集合逐个 ``(d + 6) % 7`` 换算后输出逗号列表。

    区间与步长两条展开路径共用此收尾逻辑——**先取标准集合、再逐项换算**，绝不对
    区间端点独立取模（``0-6`` 独立取模会变成 ``6-5``，首值大于末值直接撞
    APScheduler 的 ``first > last`` 校验抛 ``ValueError``，任务退化「仅手动」＝
    备份静默停止；见 :func:`_expand_weekday_range`）。
    """
    return ",".join(str((day + 6) % 7) for day in days)


def _canonical_weekday_start(value: int) -> int:
    """区间/步长**起点**归一化：标准 crontab 中 ``7`` 与 ``0`` 同为周日。

    ``7-6`` 因此等价于 ``0-6``（周日至周六＝每天）；终点不归一化——``1-7`` 需保留
    末位周日（``range(1, 8)`` 含 7）才是「周一至周日＝每天」。
    """
    return 0 if value == _CRON_WEEKDAY_MAX else value


def _expand_weekday_range(start: int, end: int) -> str | None:
    """把 ``a-b`` 数字区间展开为显式枚举；环绕（``a > b``）或越界返回 ``None``。

    Returns:
        展开后的 APScheduler 编号逗号列表；``None`` 表示**该区间非法**，调用方原样
        透传交给 APScheduler 报错（标准 cron 同样不支持环绕区间，两边口径一致）。
    """
    start = _canonical_weekday_start(start)
    if start > _CRON_WEEKDAY_MAX or end > _CRON_WEEKDAY_MAX or start > end:
        return None
    return _expand_weekday_values(range(start, end + 1))


def _expand_weekday_step(start: int, end: int, step: int) -> str:
    """把「区间 + 步长」的标准星期集合**展开**为 APScheduler 编号的逗号列表。

    为什么必须展开而不是只换算数字：APScheduler 的 ``*/N`` 从它自己的 0（周一）起算，
    标准 crontab 从 0（周日）起算——起点不同导致两组是**完全不同的星期集合**，而非
    「平移一天」：``*/2`` 标准 = {周日,周二,周四,周六}，APScheduler 原生 =
    {周一,周三,周五,周日}（两组只在周日相交）。而 APScheduler 3.x 不支持 ``6/2``
    这类带起点的步长写法（实测报 "the step value (2) is higher than the total range"），
    无法用 ``a/N`` 一步换算到位——故把标准集合逐个 ``(n + 6) % 7`` 后展开成显式枚举。
    """
    return _expand_weekday_values(range(start, end + 1, step))


def _expand_weekday_step_expression(base: str, step_text: str) -> str | None:
    """解析 ``*/N`` / ``a/N`` / ``a-b/N`` 并展开；无法识别（含越界）返回 ``None``。

    P2 护栏：步长本身也须校验上界——``*/8``、``*/99`` 这类越界步长若照
    ``range(0, 7, N)`` 展开会被静默变成「仅周日」的合法值，与越界数字（``8``、
    ``10``）「原样透传 → 报错」的处置口径不一致，属本缺陷要杜绝的「静默把非法值
    变合法值」，故一律返回 ``None`` 交 APScheduler 报错。

    Returns:
        展开后的 APScheduler 编号逗号列表；``None`` 表示**无法识别**，调用方原样
        透传该段，交给 APScheduler 抛 ``ValueError``（非法值不得静默变合法）。
    """
    if not step_text.isdigit():
        return None
    step = int(step_text)
    if step <= 0:
        return None  # ``*/0`` → APScheduler 报 "Increment must be higher than 0"
    if step > _CRON_WEEKDAY_MAX:
        return None  # ``*/8`` 等越界步长 → APScheduler 报 "... higher than the total range"
    if base == "*":
        start, end = 0, _CRON_WEEKDAY_LAST
    elif base.isdigit():
        start, end = int(base), _CRON_WEEKDAY_LAST  # ``a/N``：从 a 到星期末位
    elif "-" in base:
        left, _, right = base.partition("-")
        if not (left.isdigit() and right.isdigit()):
            return None
        start, end = int(left), int(right)
    else:
        return None
    start = _canonical_weekday_start(start)
    if start > _CRON_WEEKDAY_MAX or end > _CRON_WEEKDAY_MAX or start > end:
        return None
    return _expand_weekday_step(start, end, step)


def _shift_weekday_number(match: re.Match[str]) -> str:
    """单个星期数字换算（``(n + 6) % 7``）；越界值原样透传由 APScheduler 报错。"""
    value = int(match.group())
    if value > _CRON_WEEKDAY_MAX:
        return match.group()
    return str((value + 6) % 7)


def _shift_weekday_segment(segment: str) -> str:
    """换算星期字段的一段（``field.split(",")`` 的元素），无法识别的形态原样返回。

    - ``*``：两边同为「全部」，原样保留；
    - ``*/N`` / ``a/N`` / ``a-b/N``：展开为标准星期集合的显式枚举；
    - ``a-b``（纯数字区间）：**先展开再逐项换算**（端点独立取模会撞 APScheduler
      的 ``first > last`` 校验，见 :func:`_expand_weekday_values`）；非法区间原样透传；
    - ``a``（纯数字单点）：``(n + 6) % 7`` 换算；
    - 名称段（``mon`` / ``mon-fri`` / ``fri-mon``…）：两边语义一致，原样保留；
    - 数字与名称混杂（``0-fri`` 等畸形）：原样透传，交给 APScheduler 报错。
    """
    if _WEEKDAY_NAME_RE.search(segment):
        return segment
    base, slash, step_text = segment.partition("/")
    if slash:
        expanded = _expand_weekday_step_expression(base, step_text)
        return expanded if expanded is not None else segment
    if "-" in base:
        left, _, right = base.partition("-")
        if left.isdigit() and right.isdigit():
            expanded = _expand_weekday_range(int(left), int(right))
            return expanded if expanded is not None else segment
    return _WEEKDAY_NUMBER_RE.sub(_shift_weekday_number, base)


def _shift_weekday_field(field: str) -> str:
    """把星期字段从**标准 crontab 编号**换算为 APScheduler 3.x 编号。

    标准 crontab：``0``=周日、``1``=周一 … ``6``=周六、``7``=周日（与 0 同义）；
    APScheduler 3.x：``0``=周一 … ``6``=周日。两者正好差一天，故数字按
    ``(n + 6) % 7`` 换算（0→6、1→0、6→5、7→6）；步长写法（``*/N`` 等）因起算点
    不同而必须**展开为显式枚举**，逐段规则见 :func:`_shift_weekday_segment`。

    换算**只对数字生效**：``*`` / ``,`` / ``-`` 结构与名称写法（``mon-fri``、``sun``，
    两边语义一致）原样保留。越界数字（>7）与无法识别的形态一律原样透传，交给
    APScheduler 抛 ``ValueError``——静默把非法值取模成合法星期是本缺陷的类型，不能重犯。
    步长写法无法只换算数字（两边 ``*/N`` 起算点不同，集合而非平移的差异），故统一
    展开为显式枚举（APScheduler 3.x 不支持带起点的步长），见 :func:`_expand_weekday_step`。
    """
    return ",".join(_shift_weekday_segment(segment) for segment in field.split(","))


def _cron_trigger(cron: str, *, timezone: tzinfo) -> CronTrigger:
    """按 5 字段 cron（分 时 日 月 周）构造触发器，**星期字段按标准 crontab 语义解释**。

    为什么不直接用 ``CronTrigger.from_crontab``：APScheduler 3.x 的该方法有官方承认的
    历史缺陷——其 docstring 的 warning 原文为「Due to a historical mistake, there is a
    mismatch between weekday numbers, as APScheduler treats 0 as Monday while the
    original crontab treats it as Sunday. This has been rectified in the v4.x series but
    cannot be changed in the 3.x series due to backwards compatibility.」（上游 issue
    #286）。调用它等于把管理员填写的 cron 整体平移一天。

    本产品**必须**修正：cron 表达式直接暴露给管理员填写（E35 任务表单 / config
    ``schedule.cron_default``），管理员按标准 crontab 语义理解「``0 3 * * 1`` =
    每周一 03:00」；真机任务 #1 实测该表达式返回的下次运行落在**周二** 03:00（=
    APScheduler 原生解释），即缺陷实证。故此处自行分字段校验后显式构造
    ``CronTrigger``，仅对星期字段做 :func:`_shift_weekday_field` 换算，其余四个字段
    （分/时/日/月）两边语义一致，直接透传。

    Args:
        cron: 5 字段 cron 表达式（分 时 日 月 周）。
        timezone: 触发器时区（任务时区/config.timezone）。

    Returns:
        按标准 crontab 语义解释的 ``CronTrigger``。

    Raises:
        ValueError: 字段数非 5，或字段内容非法（由 ``CronTrigger`` 校验抛出）。
    """
    fields = cron.split()
    if len(fields) != 5:
        raise ValueError(f"cron 须为 5 字段（分 时 日 月 周），实得 {len(fields)} 字段：{cron!r}")
    minute, hour, day, month, day_of_week = fields
    return CronTrigger(
        minute=minute,
        hour=hour,
        day=day,
        month=month,
        day_of_week=_shift_weekday_field(day_of_week),
        timezone=timezone,
    )


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
        retention: RetentionService,
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
            retention: 保留服务（每日维护任务 ``maintenance-retention`` 的执行体，
                设计 §3.4/§3.5）。
        """
        self._tasks = tasks
        self._runner = runner
        self._config = config
        self._settings = settings or get_settings()
        self._jobs = jobs
        self._retention = retention
        self._scheduler = AsyncIOScheduler()
        # config 快照（next_fire_time 为同步签名，无法直接 await config 读取；
        # 由 reload()/catchup_on_startup() 入口刷新——cron_default/timezone 本批无 UI）
        self._cron_default: str | None = None
        self._tz = resolve_timezone(self._settings.tz)

    # ---------------------------------------------------------------- 生命周期（§4.3 接线点）

    async def start(self) -> None:
        """``start()`` → :meth:`reload` → :meth:`catchup_on_startup` → :meth:`mount_maintenance_jobs`。"""
        self._scheduler.start()
        await self.reload()
        await self.catchup_on_startup()
        await self.mount_maintenance_jobs()

    async def shutdown(self) -> None:
        """``scheduler.shutdown(wait=True)``——与 ``JobRunner.on_shutdown`` 顺序：先停调度再收尾进程。"""
        self._scheduler.shutdown(wait=True)

    # ---------------------------------------------------------------- 动态重载（US-026-02 AC1）

    async def reload(self) -> None:
        """全量重建 backup 触发器（保存/删除后由路由调用，「保存即生效不重启容器」）。

        ``enabled=1`` 且有效 cron 非空的任务各建一个 cron 触发器（:func:`_cron_trigger`，
        ``replace_existing``）；随后移除已不存在/已暂停/无有效 cron 任务的遗留 job。
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

    # ---------------------------------------------------------------- 维护任务域（设计 §3.5，本批新增）

    async def mount_maintenance_jobs(self) -> None:
        """挂载每日维护任务（``maintenance-*`` 前缀分域，``replace_existing`` 幂等）。

        本批（T01）仅挂 retention；check/dump 触发器随批次 B 按同一前缀追加。
        与 backup 动态重载互不干扰的关键不变量：:meth:`reload` 的 keep 集合只识别
        ``backup-task-`` 前缀，``maintenance-*`` 触发器不会被误删；本方法自身按
        ``maintenance-`` 前缀移除失效遗留（未来开关关闭时同一机制摘除）。
        """
        await self._refresh_config_snapshot()
        retention_id = f"{_MAINTENANCE_PREFIX}retention"
        self._scheduler.add_job(
            self._fire_retention,
            trigger=_cron_trigger(MAINTENANCE_RETENTION_CRON, timezone=self._tz),
            args=(),
            id=retention_id,
            replace_existing=True,
        )
        keep = {retention_id}
        for job in self._scheduler.get_jobs():
            if job.id.startswith(_MAINTENANCE_PREFIX) and job.id not in keep:
                logger.info("调度器移除遗留维护任务：%s", job.id)
                self._scheduler.remove_job(job.id)

    async def _fire_retention(self) -> None:
        """每日维护回调：执行 retention 清理；异常隔离记日志，不中断调度循环。"""
        try:
            report = await self._retention.cleanup_once()
            logger.info(
                "每日维护 retention 已执行：jobs=%s log_files=%s freed_bytes=%s",
                report.jobs_deleted,
                report.log_files_deleted,
                report.log_bytes_freed,
            )
        except Exception:  # noqa: BLE001 - 维护失败不应拖垮调度器（援 _submit 单任务隔离口径）
            logger.exception("每日维护 retention 清理失败")

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
        """prune 到期判定（§6.4 / N3）：距上次**成功 forget** 超 ``prune_interval_days``。

        语义修正（2026-10-14，随 N3 自动 forget 开放）：原实现「距上次成功 backup 超
        间隔」在备份正常进行的场景下**永不触发**（每次成功备份都把时间归零），等于
        配置形同虚设；改为以清理动作自身为基准——距上次成功 forget 超间隔即到期；
        **从未 forget 过**且存在成功备份 → 首次到期（首个备份完成即清理一次，
        之后每 interval 天一轮）。
        ``prune_interval_days <= 0`` 视为不启用（与 ``limit_*=0`` 不限同口径）。
        """
        if task.prune_interval_days <= 0:
            return False
        last_forget = await self._jobs.last_forget_success_finished_at(task.id)
        finished = parse_iso(last_forget) if last_forget else None
        if finished is None:
            # 从未清理过：有成功备份即到期（首次清理）
            return (await self._jobs.last_backup_success_finished_at(task.id)) is not None
        return datetime.now(UTC) - finished > timedelta(days=task.prune_interval_days)

    # ---------------------------------------------------------------- 自动 forget（N3 裁决开放）

    async def maybe_auto_forget(self, task_id: int) -> None:
        """backup 成功回调：到期则自动提交 forget（per-task keep 规则 + ``--prune``）。

        护栏（G3，全部前置判定，不满足静默返回）：
        - 任务存在且 ``enabled``；
        - ``prune_interval_days > 0``（显式关闭自动清理的开关）；
        - :meth:`_due_for_prune` 到期；
        - 任务至少一个 ``keep_* > 0``（0 = 未启用，全零任务**绝不**自动裁剪——
          JobRunner forget 分支的 G3 兜底也会拒绝，此处预检避免无谓落库）。

        提交走 ``trigger="scheduled"``：仓库忙时按既有语义 cancelled + 审计后跳过，
        下次 backup 成功后再判（E1 不回归）。结果经 R-018 Notifier 事后告知。
        """
        try:
            task = await self._tasks.get(task_id)
            if task is None or not task.enabled or task.prune_interval_days <= 0:
                return
            if not await self._due_for_prune(task):
                return
            keep_rules = (
                task.keep_last,
                task.keep_hourly,
                task.keep_daily,
                task.keep_weekly,
                task.keep_monthly,
                task.keep_yearly,
            )
            if all(rule <= 0 for rule in keep_rules):
                logger.info("任务 %s 未配置任何保留规则（keep_*=0），跳过自动 forget（G3）", task_id)
                return
            logger.info("任务 %s prune 到期（interval=%s 天），自动提交 forget", task_id, task.prune_interval_days)
            # keep 规则经任务 profile（task_loader）透传，与 E12 手动路径同源；
            # prune=True：自动清理的目的是回收存储，只 forget 不 prune 达不成目的。
            await self._runner.submit(
                "forget",
                task_id=task.id,
                trigger="scheduled",
                params={"prune": True},
            )
        except Exception:  # noqa: BLE001 - 自动清理失败不影响 backup 成功回调主链路
            logger.exception("自动 forget 判定/提交失败：task=%s", task_id)

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
        """按有效 cron 建触发器（星期字段走标准 crontab 语义，见 :func:`_cron_trigger`）。

        无法解析（如 6 字段秒级表达式、星期越界）时记 WARNING 并返回 None（退化「仅手动」）。
        """
        try:
            return _cron_trigger(cron, timezone=self._tz)
        except ValueError as exc:
            logger.warning(
                "任务 %s 的 cron %r 无法解析（仅收 5 字段：分 时 日 月 周）：%s；该任务不建触发器（仅手动）",
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
