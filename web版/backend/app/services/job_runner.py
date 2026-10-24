"""JobRunner（§7.1，R-004/011）：任务提交、互斥、执行、取消、启动恢复与关闭收尾。

核心不变量：
- 状态机只用 §4.5 六个值：pending/running/success/failed/interrupted/cancelled；
- 按仓库维度 ``asyncio.Lock`` 互斥（P1 单仓库，为 R-023 多仓库预留）；
- 手动触发（``manual``）遇 running/pending → 抛 :class:`JobConflictError`（409，拒绝不排队）；
  调度触发（``scheduled``）冲突**不返回 409**：按 §5.1 E12 记一条 cancelled + 审计后跳过；
- 异常路径全部收敛到 ``failed``，不泄漏内部细节到 API。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.constants import (
    ACTIVE_JOB_STATUSES,
    AUDIT_COPY_RUN,
    AUDIT_JOB_ABORT,
    AUDIT_JOB_CANCEL,
    AUDIT_JOB_FINISHED,
    AUDIT_JOB_RECOVERED,
    AUDIT_JOB_SKIPPED_CONFLICT,
    AUDIT_JOB_SUBMIT,
    AUDIT_RECOVER_RUN,
    AUDIT_REPAIR_RUN,
    BEHAVIOR_AUTO_INIT_KEY,
    BEHAVIOR_AUTO_UNLOCK_KEY,
    JOB_TYPES,
    MIRROR_REPO_IDS_MAX,
    PASSFILE_STATUS_CONFIGURED,
    REPO_PROBE_TIMEOUT_SECONDS,
    RETRY_BASE_DELAY_SECONDS,
    RETRY_COUNT_MAX,
    RETRY_MAX_DELAY_SECONDS,
)
from app.db.database import Database
from app.db.repositories.audit_repo import AuditRepo
from app.db.repositories.config_repo import ConfigRepo
from app.db.repositories.job_repo import Job, JobRepo
from app.db.repositories.repo_repo import DEFAULT_REPO_ID, Repo, ReposRepo
from app.errors import JobConflictError, NotCancellableError, NotFoundError, ValidationError
from app.exclusions import ExclusionRules
from app.services.dump import DumpService, load_dump_config
from app.services.env_sync import load_credentials, passfile_path, passfile_status
from app.services.exclude_spec import (
    assemble_excludes,
    flatten_exclude_spec,
    spec_from_wire,
)
from app.services.parser import JobAggregate, OutputParser, ProgressEvent, SnapshotRecord
from app.services.path_mapper import PathMapper
from app.services.repo_keys import neutralize_repo_key
from app.services.restic import (
    ResticCmd,
    ResticProcess,
    WhitelistArgs,
    _normalize_duration,
    failure_reason,
    secondary_repo_env,
)
from app.settings import Settings, get_settings
from app.utils.time import utc_iso
from app.utils.ulid import new_job_id

logger = logging.getLogger(__name__)

PolicyLoader = Callable[[int], Awaitable[dict[str, Any] | None]]
# task_loader 与 policy_loader 同构契约（(int) -> Awaitable[dict | None]）：
# TaskRepo.get_dict / PolicyRepo.get_dict 均按此签名注入（增量设计 §1.1 仿先例）。
TaskLoader = PolicyLoader

# E22 read_data 展示用的 check 提交参数内存保留条数上限（jobs 表无参数列、本批零 DDL；
# 进程重启后回落 False——错误列表与 summary 指标仍从日志完整还原）
CHECK_PARAMS_MEMORY_MAX = 200

# dump 阶段的适用任务类型（D2 收窄，QA 复验 2026-10-15）：仅 backup 任务链前缀
# 与独立 type=dump 任务——check/restore/forget/unlock/init **零 dump 介入**，
# 否则 dump.enabled=1 且 DB 配置故障时会阻断 AUTO_UNLOCK 等启动恢复链，
# 仓库锁残留无法自愈（设计 §3.2/§5 的「backup 任务链」意图）。
DUMP_PHASE_JOB_TYPES: tuple[str, ...] = ("backup", "dump")

# C4 多目的地：镜像阶段只服务「任务关联 + backup 类型」的 job——
# 独立提交的 backup（无 task_id）没有任务级镜像配置，也没有 task-<id> 标签可过滤。
MIRROR_PHASE_JOB_TYPES: tuple[str, ...] = ("backup",)

# 镜像失败的日志行前缀（与 [retry] / [hook] 同款：阶段留痕进任务日志，SSE 可见）
MIRROR_LOG_PREFIX = "[copy]"


@dataclass(frozen=True)
class MirrorOutcome:
    """C4 单个镜像仓库的 copy 结果（含跳过原因）。

    三种终局：成功（ok=True）、失败（ok=False 且 reason 非空）、
    **跳过**（skipped=True：镜像仓就是源仓库 / 仓不存在 / 未设密码——
    配置问题不算执行失败，与执行失败分开记账）。
    """

    repo_id: str
    ok: bool = True
    reason: str = ""
    skipped: bool = False


@dataclass(frozen=True)
class _PhaseOutcome:
    """C3 单次 restic 阶段结果（含重试次数记账）。"""

    exit_code: int
    aggregate: JobAggregate
    snapshot_id: str | None
    line_no: int
    attempt: int = 1

    def with_attempt(self, attempt: int) -> _PhaseOutcome:
        """返回附带实际尝试次数的新结果（frozen 数据类的不可变更新）。"""
        return _PhaseOutcome(
            exit_code=self.exit_code,
            aggregate=self.aggregate,
            snapshot_id=self.snapshot_id,
            line_no=self.line_no,
            attempt=attempt,
        )


class NoopNotifier:
    """通知器空实现（未注入真实 Notifier 时的缺省；接口与 :class:`app.services.notifier.Notifier` 同形）。

    R-018 落地后生产装配注入真实 Notifier（main.py）；测试与缺省路径仍用本类——
    零副作用契约不变。
    """

    async def on_start(self, job: Job) -> None:
        """空实现：任务进入 running 的通知点。"""
        return

    async def on_finish(
        self,
        job: Job,
        *,
        status: str,
        task_name: str | None = None,
        file_count: int = 0,
        bytes_done: int = 0,
        skipped_count: int = 0,
    ) -> None:
        """空实现：任务终态通知点（失败/skipped 超阈值/成功开关由真实实现分派）。"""
        return


class JobRunner:
    """任务执行器：提交/取消/执行/恢复/收尾。"""

    def __init__(
        self,
        db: Database,
        restic: ResticCmd,
        parser: OutputParser,
        audit: AuditRepo,
        notifier: NoopNotifier | None = None,
        grace_seconds: int = 30,
        db_flush_interval: float = 1.0,
        settings: Settings | None = None,
        policy_loader: PolicyLoader | None = None,
        task_loader: TaskLoader | None = None,
        repos_repo: ReposRepo | None = None,
        on_finish: Callable[[], None] | None = None,
        on_task_backup_success: Callable[[int], Awaitable[None]] | None = None,
    ) -> None:
        """构造执行器。

        Args:
            db: 数据库单例（写串行化入口）。
            restic: 命令拼装与 spawn 器。
            parser: 输出解析器（纯函数）。
            audit: 审计仓储（append-only）。
            notifier: 通知器；缺省为 :class:`NoopNotifier`。
            grace_seconds: 取消/关闭时 SIGTERM → SIGKILL 的宽限期。
            db_flush_interval: 进度落库最小间隔（秒），避免高频写 SQLite。
            settings: 运行期配置；缺省取进程单例。
            policy_loader: 策略加载回调（批次 3 由 PolicyRepo 注入；P1 可为空）。
            task_loader: 任务加载回调（选项②由 TaskRepo.get_dict 注入）；
                profile 解析时 task_id 优先于 policy_id（任务即策略，legacy 兼容）。
            repos_repo: 仓库仓储（R-023：repo_url/host_tag 的事实源；缺省按同一
                db 构造）。
            on_finish: 任务到达终态后的外部回调（如 E30 仓库状态缓存失效）；
                回调异常只记日志，不影响任务链路。
            on_task_backup_success: backup 类型任务到达 **success** 终态且 ``task_id``
                非空时以 task_id await 调用（X6：清 ``dry_run_pending``，由 main.py
                注入 ``TaskRepo.clear_dry_run_pending``）；回调异常只记 WARNING，
                不得影响 job 终态落库。
        """
        self._db = db
        self._restic = restic
        self._parser = parser
        self._audit = audit
        self._notifier = notifier or NoopNotifier()
        self._grace_seconds = max(1, grace_seconds)
        self._db_flush_interval = max(0.1, db_flush_interval)
        self._settings = settings or get_settings()
        self._policy_loader = policy_loader
        self._task_loader = task_loader
        self._on_finish = on_finish
        self._on_task_backup_success = on_task_backup_success

        self._jobs = JobRepo(db)
        self._config = ConfigRepo(db)
        self._repos = repos_repo or ReposRepo(db)
        self._exclusions = ExclusionRules(self._settings)
        self._locks: dict[str, asyncio.Lock] = {}
        self._processes: dict[str, ResticProcess] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._params: dict[str, dict[str, Any]] = {}
        # check 任务提交参数的内存保留（E22 ``read_data`` 展示；有界，见 CHECK_PARAMS_MEMORY_MAX）
        self._check_params: OrderedDict[str, dict[str, Any]] = OrderedDict()

    # ---------------------------------------------------------------- 互斥锁（§7.1）

    def _repo_lock(self, repo_url: str) -> asyncio.Lock:
        """按仓库维度惰性建锁（E1：restic 锁是仓库级的——多仓库天然并行，互不阻塞）。"""
        lock = self._locks.get(repo_url)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[repo_url] = lock
        return lock

    # ---------------------------------------------------------------- check 提交参数（E22）

    def check_params(self, job_id: str) -> dict[str, Any]:
        """读取 check 任务的提交参数（E22 ``read_data`` 展示数据源）。

        jobs 表无参数列（本批零 DDL，F17），故在内存保留最近
        ``CHECK_PARAMS_MEMORY_MAX`` 条 check 提交参数；进程重启或条目被挤出后
        返回空字典（``read_data`` 回落 False——错误列表与 summary 指标仍可从
        任务日志完整还原，不受影响）。
        """
        return dict(self._check_params.get(job_id, {}))

    def _remember_check_params(self, job_id: str, params: dict[str, Any]) -> None:
        """记录 check 提交参数（有界 FIFO：超上限淘汰最旧条目，防无界增长）。"""
        self._check_params.pop(job_id, None)
        self._check_params[job_id] = dict(params)
        while len(self._check_params) > CHECK_PARAMS_MEMORY_MAX:
            self._check_params.popitem(last=False)

    # ---------------------------------------------------------------- 提交与取消

    async def submit(
        self,
        job_type: str,
        *,
        repo_id: str = DEFAULT_REPO_ID,
        policy_id: int | None = None,
        task_id: int | None = None,
        trigger: str = "manual",
        snapshot_id: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> Job:
        """提交任务：查重 → 落 pending → 起后台执行任务。

        Args:
            job_type: 任务类型（E12 白名单）。
            repo_id: 归属仓库（R-023；互斥/凭据/密码文件都按它解析）。
            policy_id: 关联策略 id（legacy）；可为空。
            task_id: 关联备份任务 id（选项②；与 policy_id 互斥由上层校验）；
                新任务 job 落 ``task_id``、``policy_id`` 为 NULL（§1.2 决策二）。
            trigger: ``manual`` / ``scheduled``。
            snapshot_id: 关联快照（restore/forget）。
            params: 补充参数（sources/excludes/target/include…）。

        Returns:
            新建任务（manual 冲突时抛错；scheduled 冲突时返回一条 cancelled 任务）。

        Raises:
            JobConflictError: manual 触发且**同仓库**已有 running/pending 任务（409，
                data 带 ``running_job_id`` 与 ``running_task_name``）。
        """
        if job_type not in JOB_TYPES:
            raise ValidationError(f"不支持的任务类型：{job_type}", data={"allowed": list(JOB_TYPES)})
        repo_url = await self._repo_url(repo_id)
        lock = self._repo_lock(repo_url)

        async with lock:
            # E1 仓库维度互斥：只查**同仓库**的活跃任务——多仓库互不阻塞
            active = await self._jobs.list_active(repo_id)
            if active:
                conflict = active[0]
                if trigger == "manual":
                    raise JobConflictError(
                        data={
                            "running_job_id": conflict.id,
                            # US-026-04 AC2：UI 提示「已有任务进行中：<任务名>」；
                            # 运行中 job 无任务关联（legacy 策略 job）时为 None。
                            "running_task_name": await self._task_name(conflict.task_id),
                        }
                    )
                # scheduled 冲突：不返回 409，落一条 cancelled + 审计后跳过（§5.1 E12 注）
                skipped = await self._jobs.create(
                    job_id=new_job_id(),
                    job_type=job_type,
                    trigger=trigger,
                    log_path=str(self._settings.jobs_dir / f"{job_type}-skipped.log"),
                    repo_id=repo_id,
                    policy_id=policy_id,
                    task_id=task_id,
                    snapshot_id=snapshot_id,
                    status="cancelled",
                )
                await self._audit.record(
                    AUDIT_JOB_SKIPPED_CONFLICT,
                    target=skipped.id,
                    detail={"job_type": job_type, "conflict_job_id": conflict.id, "task_id": task_id},
                    result="denied",
                )
                return skipped

            job_id = new_job_id()
            log_path = str(self._settings.jobs_dir / f"{job_id}.log")
            job = await self._jobs.create(
                job_id=job_id,
                job_type=job_type,
                trigger=trigger,
                log_path=log_path,
                repo_id=repo_id,
                policy_id=policy_id,
                task_id=task_id,
                snapshot_id=snapshot_id,
            )

        self._params[job_id] = dict(params or {})
        if job_type == "check":
            self._remember_check_params(job_id, self._params[job_id])
        if job_type == "repair":
            # B1 自愈：repair 属危险操作（重建索引/卷，不可逆提示在 UI ConfirmGate），
            # 单独埋点 repair.run 入审计（与 AUDIT_JOB_SUBMIT 互补）。
            await self._audit.record(
                AUDIT_REPAIR_RUN,
                target=job_id,
                detail={"repair_target": str(params.get("repair_target") or "index"), "repo_id": repo_id},
                result="ok",
            )
        if job_type == "recover":
            # B3 自愈：recover 找回丢失索引的快照（写仓库元数据，同属危险操作留痕）。
            await self._audit.record(
                AUDIT_RECOVER_RUN,
                target=job_id,
                detail={"repo_id": repo_id},
                result="ok",
            )
        await self._audit.record(
            AUDIT_JOB_SUBMIT,
            target=job_id,
            detail={
                "type": job_type,
                "trigger": trigger,
                "repo_id": repo_id,
                "policy_id": policy_id,
                "task_id": task_id,
            },
            result="ok",
        )
        self._tasks[job_id] = asyncio.create_task(self._run(job), name=f"job-{job_id}")
        return job

    async def cancel(self, job_id: str, *, force: bool = False) -> Job:
        """取消任务：SIGTERM → 宽限 grace_seconds → SIGKILL，终态 cancelled。

        ``force=True`` 对应 P7 D2 的 **Abort 二段**（Duplicati isStopping→abort 语义，
        方案 §4.5.4「Abort = grace=0 直杀 + force 参数 + 审计埋点」）：跳过 SIGTERM 与
        宽限等待，直接 SIGKILL 并立即收尾——用于「已请求停止但迟迟不退出」的场景。
        审计按动作分流：``job.abort``（force）/ ``job.cancel``（普通），两者 detail 均只记
        类型与宽限值，**不含命令行原文**（S3 延续）。

        Args:
            job_id: 任务 id。
            force: True = 跳过宽限直接强杀（Abort）；False = 常规 SIGTERM → grace → SIGKILL。

        Returns:
            更新后的任务。

        Raises:
            NotFoundError: 任务不存在（404）。
            NotCancellableError: 任务非 running/pending（409）。
        """
        job = await self._jobs.get(job_id)
        if job is None:
            raise NotFoundError("任务不存在")
        if job.status not in ACTIVE_JOB_STATUSES:
            raise NotCancellableError(data={"status": job.status})

        proc = self._processes.get(job_id)
        if proc is not None:
            if force:
                # Abort：跳过宽限，直接 SIGKILL（restic 未落盘的部分由下次增量补齐）
                logger.warning("任务 %s Abort（force）：跳过 %ss 宽限，直接 SIGKILL", job_id, self._grace_seconds)
                proc.kill()
                await proc.wait()
            else:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), self._grace_seconds)
                except TimeoutError:
                    logger.warning("任务 %s 宽限 %ss 后仍未退出，发送 SIGKILL", job_id, self._grace_seconds)
                    proc.kill()
                    await proc.wait()

        task = self._tasks.pop(job_id, None)
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        await self._jobs.finalize(
            job_id, "cancelled", exit_code=-1, summary="用户强制中止（Abort）" if force else "用户取消"
        )
        await self._audit.record(
            AUDIT_JOB_ABORT if force else AUDIT_JOB_CANCEL,
            target=job_id,
            detail={"type": job.type, "force": force, "grace_seconds": 0 if force else self._grace_seconds},
            result="ok",
        )
        updated = await self._jobs.get(job_id)
        return updated or job

    # ---------------------------------------------------------------- 执行主链路（§6.2）

    async def _run(self, job: Job) -> None:
        """执行单个任务：抢锁 → running → spawn → 逐行解析落库 → 终态 → 审计/通知。

        任务级回调（``on_task_backup_success``）**必须在锁外**执行：它可能再次
        ``submit``（N3 自动 forget），而 ``asyncio.Lock`` 不可重入——锁内 await 会
        让本任务永久等待自己持有的锁，锁再不释放，后续所有提交一并挂死。
        """
        repo_id = job.repo_id
        repo_url = await self._repo_url(repo_id)
        # 锁外回调的入参快照（锁内赋值、锁外消费；None = 不需要回调）
        backup_success_task_id: int | None = None
        async with self._repo_lock(repo_url):
            line_no = 0
            try:
                await self._jobs.update_status(job.id, "running", started_at=utc_iso())
                # R-018：healthchecks /start ping（真实 Notifier）；空实现零副作用。
                await self._safe_notify_start(job)

                # 任务 profile（C3 重试 / C4 镜像共用；仅任务关联 job 才有）
                profile = await self._load_profile(job)

                # dump 阶段（R-019，US-P2-09）：**仅** backup 任务链前缀（含独立
                # type=dump）执行——失败 → failed 终态 + 告警，**阻断** backup 阶段
                # （同锁内，E1 不回归）；其余类型零 dump 介入（D2 收窄，见
                # DUMP_PHASE_JOB_TYPES 注释），dump 故障绝不阻断 unlock 等恢复链。
                if job.type in DUMP_PHASE_JOB_TYPES:
                    dump_ok, dump_reason = await self._run_dump_phase(job)
                    if not dump_ok:
                        await self._fail_in_phase(job, f"dump 阶段失败：{dump_reason}", phase="dump")
                        return
                    if job.type == "dump":
                        # 独立 type=dump 任务（E12 触发，测试用途）：无 restic 阶段，dump 即终态
                        await self._finish_dump_only(job)
                        return

                argv, env = await self._prepare(job, repo_url, repo_id, profile)
                # C3 重试：restic 阶段按任务级 retry_count 自动重试（仅 scheduled 触发）
                attempts = 1 + self._retry_budget(job, profile)
                phase = await self._run_restic_phase(
                    job,
                    argv,
                    env,
                    log_path=Path(job.log_path),
                    start_line_no=line_no,
                    attempts=attempts,
                )
                line_no = phase.line_no
                exit_code = phase.exit_code
                aggregate = phase.aggregate
                snapshot_id = phase.snapshot_id
                status = "success" if exit_code == 0 else "failed"
                # C3 记账：重试次数进摘要（UI 一眼看到「这次是重试后成的」）
                retry_note = f"；重试 {phase.attempt - 1} 次后成功" if phase.attempt > 1 else ""
                # C4 多目的地：镜像阶段在**主备份成功后**才跑（主仓失败就没有新快照
                # 可复制，跑 copy 只会把上一次的旧快照再传一遍）；
                # 镜像失败不翻转 status——主仓数据已落好，标 failed 是谎报。
                mirror_note = ""
                if (
                    status == "success"
                    and job.type in MIRROR_PHASE_JOB_TYPES
                    and job.task_id is not None
                ):
                    mirror_ids = _as_mirror_ids(profile.get("mirror_repo_ids"))
                    if mirror_ids:
                        line_no, mirrors = await self._run_mirror_phase(
                            job, mirror_ids, log_path=Path(job.log_path), start_line_no=line_no
                        )
                        mirror_note = _mirror_note(mirrors)
                await self._jobs.finalize(
                    job.id,
                    status,
                    exit_code=exit_code,
                    summary=self._summarize(status, aggregate, exit_code)
                    + retry_note
                    + mirror_note,
                    bytes_done=aggregate.bytes_done,
                    files_done=aggregate.files_done,
                    skipped_count=aggregate.skipped_count,
                    last_event_id=line_no,
                )
                # init 成功后 neutral 化 key：把唯一密钥的明文身份标注（username/hostname）
                # 换成中性值并移除旧 key（``keys/`` 是仓库唯一明文文件，见 repo_keys 模块）。
                # 失败只记日志、绝不翻转已落库的 success 终态（加固步骤不能反噬主流程）。
                if (
                    status == "success"
                    and job.type == "init"
                    and env.get("RESTIC_PASSWORD_FILE")
                ):
                    with contextlib.suppress(Exception):
                        await neutralize_repo_key(
                            self._restic,
                            repo_id=repo_id,
                            env=env,
                            password_file=env["RESTIC_PASSWORD_FILE"],
                        )
                if snapshot_id and snapshot_id != job.snapshot_id:
                    await self._jobs.set_snapshot(job.id, snapshot_id)
                await self._audit.record(
                    AUDIT_JOB_FINISHED,
                    target=job.id,
                    detail={
                        "type": job.type,
                        "status": status,
                        "exit_code": exit_code,
                        "skipped_count": aggregate.skipped_count,
                        "bytes_done": aggregate.bytes_done,
                        # C3：尝试次数（1 = 一次通过；>1 = 重试后成 / 重试后仍败）
                        "attempts": phase.attempt,
                    },
                    result="ok" if status == "success" else "failed",
                )
                # R-018 终态通知（失败/skipped 超阈值恒告警；成功仅 on_success 开启时）。
                # 外壳吞异常：此处若上抛会落进 except Exception 把已落库 success 篡改为 failed。
                await self._safe_notify_finish(
                    job,
                    status=status,
                    file_count=aggregate.files_done,
                    bytes_done=aggregate.bytes_done,
                    skipped_count=aggregate.skipped_count,
                )
                if status == "success" and job.type == "backup" and job.task_id is not None:
                    # X6（owner 裁决）：「E36 预览成功**或首次成功备份**」清 dry_run_pending
                    # 的后半——绕过 UI 直接成功备份同样解除 Start 拦截（R-026-08）。
                    # 只**记账**不调用：实际回调在锁外执行（见方法 docstring「自死锁」）。
                    backup_success_task_id = job.task_id
            except asyncio.CancelledError:
                logger.info("任务 %s 被取消，执行链路退出（终态由 cancel() 落库）", job.id)
                raise
            except Exception as exc:  # noqa: BLE001 - 所有异常收敛为 failed
                logger.exception("任务 %s 执行失败", job.id)
                await self._jobs.finalize(
                    job.id,
                    "failed",
                    exit_code=-1,
                    summary=f"执行失败：{type(exc).__name__}",
                    last_event_id=line_no,
                )
                await self._audit.record(
                    AUDIT_JOB_FINISHED,
                    target=job.id,
                    detail={"type": job.type, "status": "failed", "error": type(exc).__name__},
                    result="failed",
                )
                # R-018：异常路径同样要触发失败告警（外壳吞异常，不影响已落库终态）
                await self._safe_notify_finish(job, status="failed")
            finally:
                self._processes.pop(job.id, None)
                self._params.pop(job.id, None)
                self._tasks.pop(job.id, None)
                self._notify_finish()

        # N3 自死锁修复（真机取证 2026-10-25）：此处已在 ``async with`` 之外，仓库锁
        # 已释放——自动 forget 的 ``submit`` 才能拿到锁。回调内自带异常吞没，无需再包。
        if backup_success_task_id is not None:
            await self._notify_task_backup_success(backup_success_task_id)

    def _notify_finish(self) -> None:
        """任务终态后的外部回调（当前用途：E30 仓库状态缓存失效）。

        init/unlock 等任务会改变仓库可连性——不失效缓存的话，用户跑完 init
        再看状态卡，最多 30s 内拿到的还是"未连接"的旧值。
        """
        if self._on_finish is None:
            return
        try:
            self._on_finish()
        except Exception:  # noqa: BLE001 - 回调只做辅助通知，失败不能影响任务链路
            logger.exception("JobRunner on_finish 回调执行失败")

    async def _safe_notify_start(self, job: Job) -> None:
        """任务进入 running 的通知点（R-018 on_start）。异常就地吞掉——通知绝不影响任务链路，
        尤其此处失败若上抛会落进 except Exception 把任务打成 failed。"""
        try:
            await self._notifier.on_start(job)
        except Exception:  # noqa: BLE001 - 通知失败不能影响任务链路
            logger.warning("JobRunner on_start 通知执行失败（job=%s）", job.id, exc_info=True)

    async def _safe_notify_finish(
        self,
        job: Job,
        *,
        status: str,
        file_count: int = 0,
        bytes_done: int = 0,
        skipped_count: int = 0,
    ) -> None:
        """任务终态通知点（R-018 on_finish）。异常就地吞掉——调用点在 finalize 之后，
        上抛会被外层 except 再次 finalize，篡改已落库终态（见 _run 内注释）。"""
        try:
            await self._notifier.on_finish(
                job,
                status=status,
                task_name=await self._task_name(job.task_id),
                file_count=file_count,
                bytes_done=bytes_done,
                skipped_count=skipped_count,
            )
        except Exception:  # noqa: BLE001 - 通知失败不能影响任务链路
            logger.warning("JobRunner on_finish 通知执行失败（job=%s）", job.id, exc_info=True)

    async def _notify_task_backup_success(self, task_id: int) -> None:
        """backup 类型任务 success 终态后的任务级回调（X6：清 ``dry_run_pending``）。

        沿 ``on_finish`` 回调先例（main.py 注入 ``TaskRepo.clear_dry_run_pending``，
        A1 合规：业务层不写 SQL）。异常必须就地吞掉——本方法在 ``_run`` 的 try 块内
        执行，回调若抛异常会被外层 ``except Exception`` 收敛为 failed 终态，篡改
        已落库的 success。
        """
        if self._on_task_backup_success is None:
            return
        try:
            await self._on_task_backup_success(task_id)
        except Exception:  # noqa: BLE001 - 回调只做辅助状态推进，失败不能影响任务链路
            logger.warning(
                "JobRunner on_task_backup_success 回调执行失败（task=%s）", task_id, exc_info=True
            )

    # ---------------------------------------------------------------- dump 阶段（R-019）

    async def _run_dump_phase(self, job: Job) -> tuple[bool, str]:
        """执行 dump 阶段（仅 :data:`DUMP_PHASE_JOB_TYPES` 生效；US-P2-09）。

        成功后随即按 ``dump.retention_days`` 清理一轮产物（Q10 基线：每次成功 dump
        后执行一次；``<=0`` 由 :meth:`DumpService.cleanup` 内部跳过）。

        Returns:
            ``(成功与否, 失败原因)``；未启用时返回 ``(True, "")``——与 dump 关闭时
            行为逐字节一致（回归保护）。
        """
        try:
            cfg = await load_dump_config(self._config)
        except Exception as exc:  # noqa: BLE001 - 配置非法也要收敛为阶段失败（不 5xx）
            logger.warning("dump 配置装配失败：job=%s %s", job.id, exc)
            return False, str(exc)
        if cfg is None:
            return True, ""
        service = DumpService(self._config, self._settings)
        try:
            if cfg.engine == "pg":
                await service.probe_pg(cfg)
            await service.run(cfg, tag=f"job-{job.id}")
            await asyncio.to_thread(service.cleanup, cfg.retention_days)
        except Exception as exc:  # noqa: BLE001 - dump 失败阻断 backup，原因进 summary
            logger.exception("dump 阶段失败：job=%s", job.id)
            return False, str(exc)
        return True, ""

    # ---------------------------------------------------------------- C3 任务级重试（number-of-retries）

    @staticmethod
    def _retry_budget(job: Job, profile: dict[str, Any]) -> int:
        """C3 重试预算（追加在首次执行之上的**额外**尝试次数）。

        - 手动触发（``manual``）恒 0：用户点「立即运行」是立刻要看结果，自动重试
          会把它变成不可预期的长任务（且进度条会二次回卷，观感更差）；
        - 任务级 ``retry_count`` 钳 [0, RETRY_COUNT_MAX]；无任务关联（legacy 策略
          job）或值非法 → 0（fail-safe：不重试绝不比乱重试更糟）。
        """
        if job.trigger != "scheduled":
            return 0
        try:
            value = int(profile.get("retry_count") or 0)
        except (TypeError, ValueError):
            return 0
        return max(0, min(value, RETRY_COUNT_MAX))

    async def _run_restic_phase(
        self,
        job: Job,
        argv: list[str],
        env: dict[str, str],
        *,
        log_path: Path,
        start_line_no: int,
        attempts: int,
    ) -> _PhaseOutcome:
        """执行 restic 阶段并在失败时按预算重试（指数退避）。

        **只对 restic 阶段重试**（刻意设计，文档同步）：dump 阶段失败多为配置/版本
        问题（pg 客户端版本不匹配之类），重试无益且会拖长任务；restic 失败才有
        「锁冲突 exit 11 / 远端瞬时错误 / 网络抖动」这类重试能救的场景。

        每次尝试**重新计数**聚合（不累加）：进度不应因重试而翻倍，最终落库的是
        **最后一次**尝试的结果。
        """
        line_no = start_line_no
        attempt = 0
        while True:
            attempt += 1
            result = await self._spawn_and_consume(
                job, argv, env, log_path=log_path, start_line_no=line_no
            )
            line_no = result.line_no
            if result.exit_code == 0 or attempt >= attempts:
                return result.with_attempt(attempt)
            delay = _retry_delay(attempt)
            await self._log_retry(job, log_path, attempt=attempt, attempts=attempts, delay=delay)
            await asyncio.sleep(delay)

    # ---------------------------------------------------------------- C4 多目的地镜像

    async def _run_mirror_phase(
        self,
        job: Job,
        mirror_ids: tuple[str, ...],
        *,
        log_path: Path,
        start_line_no: int,
    ) -> tuple[int, list[MirrorOutcome]]:
        """备份成功后把本任务的新快照**增量**复制到各镜像仓（逐个串行）。

        设计取舍（逐条对应《Duplicati功能对齐分析与改造计划》批次 D C4）：

        - **串行而非并行**：copy 要读源仓索引再写目的仓，并行 N 份会让 IO 与带宽
          同时翻 N 倍；串行还保证日志顺序可读（并行输出会互相穿插）。
        - **失败隔离**：某个镜像失败不影响其它镜像继续，也**不降级主目的地终态**——
          主仓数据已经落好了，标 failed 是谎报（Q4 静默防线的反面：不许报假）。
        - **增量由 restic 保证**：``--tag task-<id>`` 过滤本任务快照，restic 侧按
          快照 Original 字段跳过目的仓已有的（cmd_copy.go collectAllSnapshots），
          重复运行只传新快照，且上一次失败的会在下一次补齐（自愈，无需手工补）。
        - 数量上限 :data:`~app.constants.MIRROR_REPO_IDS_MAX`（写侧已钳，此处再兜）。

        Returns:
            ``(最新行号, 各镜像结果列表)``——行号用于续接后续阶段的事件 id。
        """
        line_no = start_line_no
        outcomes: list[MirrorOutcome] = []
        for mirror_id in mirror_ids[:MIRROR_REPO_IDS_MAX]:
            outcome, line_no = await self._mirror_copy(job, mirror_id, log_path, line_no)
            outcomes.append(outcome)
        return line_no, outcomes

    async def _mirror_copy(
        self,
        job: Job,
        mirror_id: str,
        log_path: Path,
        start_line_no: int,
    ) -> tuple[MirrorOutcome, int]:
        """单个镜像仓的 copy：跳过条件全部显式记账（绝不静默跳过）。

        三个前置检查都在 spawn 之前完成——缺口令文件/仓不存在这类配置问题不该
        变成一次必然失败的 restic 调用（还会污染日志与审计）。
        """
        if mirror_id == job.repo_id:
            # 自指也走 _finish_mirror 记账（日志+审计 skipped），绝不静默返回
            outcome = MirrorOutcome(mirror_id, skipped=True, reason="与源仓库相同（已跳过）")
            return await self._finish_mirror(job, outcome, log_path, start_line_no, exit_code=0)
        mirror = await self._repos.get(mirror_id)
        if mirror is None or not (mirror.url or "").strip():
            outcome = MirrorOutcome(
                mirror_id, ok=False, reason=f"镜像仓 {mirror_id} 不存在或未配置仓库地址"
            )
            return await self._finish_mirror(job, outcome, log_path, start_line_no, exit_code=-1)
        if passfile_status(self._settings, mirror_id) != PASSFILE_STATUS_CONFIGURED:
            outcome = MirrorOutcome(mirror_id, ok=False, reason=f"镜像仓 {mirror_id} 未设置仓库密码")
            return await self._finish_mirror(job, outcome, log_path, start_line_no, exit_code=-1)

        src_url = await self._repo_url(job.repo_id)
        env_extras = await self._load_credentials(job.repo_id)
        # 目的仓的 url 与口令文件都走 env（与源仓同口径），argv 上干干净净（S3）
        env_extras.update(secondary_repo_env(mirror.url, passfile_path(self._settings, mirror_id)))
        args = WhitelistArgs(json_output=False, tags=(f"task-{job.task_id}",))
        argv = self._restic.build("copy", args=args, env_extras=env_extras)
        env = self._restic.build_env(repo_url=src_url, repo_id=job.repo_id, env_extras=env_extras)
        line_no, exit_code, last_line = await self._consume_copy(
            job, argv, env, log_path, start_line_no
        )
        outcome = MirrorOutcome(
            mirror_id,
            ok=exit_code == 0,
            reason="" if exit_code == 0 else failure_reason(exit_code, [last_line]),
        )
        return await self._finish_mirror(job, outcome, log_path, line_no, exit_code=exit_code)

    async def _finish_mirror(
        self,
        job: Job,
        outcome: MirrorOutcome,
        log_path: Path,
        line_no: int,
        *,
        exit_code: int,
    ) -> tuple[MirrorOutcome, int]:
        """镜像结果落三处：任务日志（SSE 可见）+ 审计 + 返回值。"""
        verb = "跳过" if outcome.skipped else ("成功" if outcome.ok else "失败")
        tail = f"：{outcome.reason}" if outcome.reason else ""
        line = f"{MIRROR_LOG_PREFIX} → {outcome.repo_id} {verb}{tail}"
        logger.info("任务 %s %s", job.id, line)
        try:
            await asyncio.to_thread(_append_log_sync, log_path, line)
            line_no += 1
        except OSError:  # 日志写失败不应打断镜像链
            logger.warning("任务 %s 镜像日志写入失败", job.id)
        await self._audit.record(
            AUDIT_COPY_RUN,
            target=job.id,
            detail={
                "type": job.type,
                "source_repo": job.repo_id,
                "target_repo": outcome.repo_id,
                "status": "skipped" if outcome.skipped else ("ok" if outcome.ok else "failed"),
                "exit_code": exit_code,
            },
            result="ok" if outcome.ok or outcome.skipped else "failed",
        )
        return outcome, line_no

    async def _consume_copy(
        self,
        job: Job,
        argv: list[str],
        env: dict[str, str],
        log_path: Path,
        start_line_no: int,
    ) -> tuple[int, int, str]:
        """执行 copy 子进程并消费输出（返回行号 / 退出码 / 最后一行非空输出）。

        与 :meth:`_spawn_and_consume` 的差异：copy **没有 JSON 输出**（cmd_copy.go
        硬编码 ``NewProgressPrinter(false, …)``，``--json`` 对它无效），故不进
        OutputParser——逐行只做 tee 落盘与行号递增，最后一行留作失败原因线索。
        循环必须跑完：``ResticProcess.lines()`` 兼做落盘，半途退出会堵住 stdout。
        """
        proc = await self._restic.spawn(argv, log_path, env=env)
        self._processes[job.id] = proc
        line_no = start_line_no
        last_line = ""
        async for line in proc.lines():
            line_no += 1
            if line.strip():
                last_line = line.strip()
        exit_code = await proc.wait()
        return line_no, exit_code, last_line

    async def _spawn_and_consume(
        self,
        job: Job,
        argv: list[str],
        env: dict[str, str],
        *,
        log_path: Path,
        start_line_no: int,
    ) -> _PhaseOutcome:
        """单次 restic 执行：spawn → 逐行解析落库 → 等退出码（与原行为逐字节一致）。"""
        proc = await self._restic.spawn(argv, log_path, env=env)
        self._processes[job.id] = proc

        aggregate = JobAggregate()
        snapshot_id = job.snapshot_id
        line_no = start_line_no
        last_flush = time.monotonic()

        async for line in proc.lines():
            line_no += 1
            event = await asyncio.to_thread(self._parser.parse_line, line)
            if isinstance(event, ProgressEvent):
                event.event_id = line_no
                aggregate = self._parser.aggregate(aggregate, event)
            elif isinstance(event, SnapshotRecord):
                if event.snapshot_id:
                    snapshot_id = event.snapshot_id
                # X12：restore 的 summary 用 files_skipped 记录因权限等原因
                # 未被恢复的文件数（--overwrite=always 下仍写不进去即计入；
                # 该行不产生 ProgressEvent，此前被整体丢弃 → skipped_count
                # 恒 0、任务静默报 success，见架构文档 §12 V7 / 缺陷 X12）。
                # 此处把 files_skipped 折入聚合，让 skipped_count / 审计 /
                # SSE / UI 反映真实的部分失败。restic summary 每任务唯一一行
                # （输出末尾），折一次即正确、不重复累加；backup summary 无
                # 该字段（None）→ 不进本分支，行为逐字节不变。
                if event.files_skipped is not None:
                    aggregate = self._parser.aggregate(
                        aggregate, ProgressEvent(skipped_delta=event.files_skipped)
                    )
            if time.monotonic() - last_flush >= self._db_flush_interval:
                await self._jobs.update_progress(
                    job.id,
                    bytes_done=aggregate.bytes_done,
                    files_done=aggregate.files_done,
                    skipped_count=aggregate.skipped_count,
                    last_event_id=line_no,
                )
                last_flush = time.monotonic()

        exit_code = await proc.wait()
        return _PhaseOutcome(
            exit_code=exit_code, aggregate=aggregate, snapshot_id=snapshot_id, line_no=line_no
        )

    async def _log_retry(
        self, job: Job, log_path: Path, *, attempt: int, attempts: int, delay: float
    ) -> None:
        """把重试决定写进任务日志（SSE/E27 可见，禁止静默重试）。"""
        line = (
            f"[retry] 第 {attempt}/{attempts - 1} 次重试将于 {delay:g}s 后开始"
            f"（attempt={attempt + 1}/{attempts}）"
        )
        logger.info("任务 %s 失败，%s", job.id, line)
        try:
            await asyncio.to_thread(_append_log_sync, log_path, line)
        except OSError:  # 日志写失败不应打断重试判定
            logger.warning("任务 %s 重试日志写入失败", job.id)

    async def _fail_in_phase(self, job: Job, summary: str, *, phase: str) -> None:
        """阶段失败收敛：failed 终态 + 审计 + 告警（与 _run 异常路径同口径，不 5xx）。"""
        await self._jobs.finalize(
            job.id, "failed", exit_code=-1, summary=summary, last_event_id=0
        )
        await self._audit.record(
            AUDIT_JOB_FINISHED,
            target=job.id,
            detail={"type": job.type, "status": "failed", "phase": phase},
            result="failed",
        )
        # R-018：dump 失败必须告警（外壳吞异常，不影响已落库终态）
        await self._safe_notify_finish(job, status="failed")

    async def _finish_dump_only(self, job: Job) -> None:
        """独立 type=dump 任务的终态收尾（dump 即终态，无 restic 阶段）。"""
        await self._jobs.finalize(
            job.id, "success", exit_code=0, summary="dump 阶段完成", last_event_id=0
        )
        await self._audit.record(
            AUDIT_JOB_FINISHED,
            target=job.id,
            detail={"type": job.type, "status": "success", "phase": "dump"},
            result="ok",
        )
        await self._safe_notify_finish(job, status="success")

    # ---------------------------------------------------------------- 启动恢复 / 关闭收尾

    async def recover_on_startup(self) -> int:
        """启动扫描：遗留 running/pending 改标 interrupted（§6.5）。

        Returns:
            被改标的任务条数。
        """
        # pending 在 P1 无排队语义，容器重启即不可恢复，故与 running 一并置 interrupted
        affected = await self._jobs.mark_interrupted_on_startup()
        if affected:
            logger.warning("启动恢复：%d 个遗留任务改标 interrupted", affected)
            await self._audit.record(
                AUDIT_JOB_RECOVERED,
                target="jobs",
                detail={"count": affected},
                result="ok",
            )
        # AUTO_INIT/AUTO_UNLOCK（Q8 基线，§6.5）：开关默认 off——off 时本分支
        # 不产生任何 restic 调用，行为与旧实现逐字节一致（回归保护）。
        await self._auto_recover_repo()
        return affected

    async def _auto_recover_repo(self) -> None:
        """AUTO_UNLOCK / AUTO_INIT 分支（Q8 基线）：锁残留自动清锁、未初始化仓库自动 init。

        - ``behavior.auto_unlock=1``：提交一次 unlock 任务清理锁残留（unlock 幂等，
          无锁时无害）；审计沿用 job.submit/job.finished 既有埋点；
        - ``behavior.auto_init=1``：探测仓库未初始化（``cat config`` 报「仓库不存在/
          未初始化」关键词）时提交一次 init 任务；已初始化或因锁/网络探测失败时
          **不动**（fail-safe：误 init 已初始化仓库会报错并污染任务历史）。

        补跑本身由 ``SchedulerService.catchup_on_startup`` 承担（启动时序晚于本方法）。
        R-023：启动期自动任务只覆盖 **default** 仓库（env 覆盖锚点）；其余仓库的
        锁残留/未初始化由各仓库首次任务或人工处理（多仓库全量自愈超出 P3-C 范围）。
        """
        repo_url = await self._repo_url(DEFAULT_REPO_ID)
        if not repo_url:
            return  # 仓库未配置：unlock/init 都无从谈起（避免无谓 failed 任务）

        if await self._behavior_flag(BEHAVIOR_AUTO_UNLOCK_KEY):
            logger.warning("AUTO_UNLOCK 开启：提交 unlock 清理仓库锁残留")
            await self._submit_auto("unlock")

        if (
            await self._behavior_flag(BEHAVIOR_AUTO_INIT_KEY)
            and not await self._repo_initialized(repo_url, DEFAULT_REPO_ID)
        ):
            logger.warning("AUTO_INIT 开启且仓库未初始化：提交 init")
            await self._submit_auto("init")

    async def _behavior_flag(self, key: str) -> bool:
        """读取 behavior.* 开关（仅 ``"1"`` 为开；缺省/其他值一律 off，fail-safe）。"""
        return ((await self._config.get_value(key)) or "").strip() == "1"

    async def _submit_auto(self, job_type: str) -> None:
        """提交启动期自动任务（unlock/init）；失败只记日志，不阻断启动流程。"""
        try:
            await self.submit(job_type, trigger="scheduled")
        except Exception:  # noqa: BLE001 - 自动恢复失败不应拖垮应用启动
            logger.exception("启动期自动任务提交失败：%s", job_type)

    async def _repo_initialized(self, repo_url: str, repo_id: str = DEFAULT_REPO_ID) -> bool:
        """探测仓库是否已初始化（``cat config``；未初始化关键词命中即 False）。

        探测失败但非「未初始化」（锁残留/网络不可达）时返回 True——交给 AUTO_UNLOCK
        或人工处理，绝不在此路径误触发 init。
        """
        argv = self._restic.build("cat", args=WhitelistArgs(json_output=False, extra_positionals=("config",)))
        env = self._restic.build_env(
            repo_url=repo_url, repo_id=repo_id, env_extras=await self._load_credentials(repo_id)
        )
        probe = await self._restic.run_json(
            argv, env=env, timeout_seconds=REPO_PROBE_TIMEOUT_SECONDS
        )
        if probe.exit_code == 0:
            return True
        joined = "\n".join(probe.lines).lower()
        not_initialized = any(
            keyword in joined
            for keyword in ("unable to open config file", "not initialized", "does not exist")
        )
        if not_initialized:
            return False
        logger.warning("仓库初始化探测未定性（退出码 %s），跳过 AUTO_INIT", probe.exit_code)
        return True

    async def on_shutdown(self) -> None:
        """容器 stop 收尾：向 restic 子进程转发 SIGTERM，宽限后 SIGKILL（§6.5）。

        只做进程收尾，**不改任务状态**——遗留任务由下次启动的
        :meth:`recover_on_startup` 统一改标 interrupted。

        无子进程但仍有未收尾的后台任务时（如任务刚 submit 尚未 spawn）同样要走
        取消+ gather 路径：否则任务可能在与 ``close_database`` 的竞态中带着
        「Connection closed」异常孤立结束（asyncio 报 Task exception was never
        retrieved，且 ``_tasks`` 条目残留）。
        """
        if not self._processes and not self._tasks:
            return
        logger.info("关闭收尾：向 %d 个 restic 子进程转发 SIGTERM", len(self._processes))
        for proc in self._processes.values():
            proc.terminate()
        await asyncio.gather(*(self._wait_or_kill(proc) for proc in list(self._processes.values())))
        tasks = list(self._tasks.values())
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            # 等待后台任务真正退出：lifespan 随后会 close_database，
            # 若不等待，任务内的 finalize 落库会撞已关闭连接（QA 实测
            # "Task exception was never retrieved: ValueError: Connection closed"）。
            # return_exceptions=True：吞掉 CancelledError 与收尾期异常，保证有序失败。
            await asyncio.gather(*tasks, return_exceptions=True)
        self._processes.clear()
        self._tasks.clear()

    async def _wait_or_kill(self, proc: ResticProcess) -> None:
        """等待子进程退出；宽限期超时后强制 SIGKILL。"""
        try:
            await asyncio.wait_for(proc.wait(), self._grace_seconds)
        except TimeoutError:
            proc.kill()
            with contextlib.suppress(Exception):
                await proc.wait()

    # ---------------------------------------------------------------- 命令拼装

    async def _prepare(
        self,
        job: Job,
        repo_url: str,
        repo_id: str = DEFAULT_REPO_ID,
        profile: dict[str, Any] | None = None,
    ) -> tuple[list[str], dict[str, str]]:
        """拼装 argv 与子进程环境（凭据/密码文件均按 ``repo_id`` 解析）。

        Args:
            profile: 已加载的任务/策略 profile（C1：``_run`` 内为取钩子命令已加载
                一次，此处复用，避免同一任务重复读库）；缺省时自行加载。
        """
        params = self._params.get(job.id, {})
        if profile is None:
            profile = await self._load_profile(job)
        args = self._build_args(job.type, job.snapshot_id, params, profile)
        env_extras = await self._load_credentials(repo_id)
        argv = self._restic.build(job.type, args=args, env_extras=env_extras)
        env = self._restic.build_env(repo_url=repo_url, repo_id=repo_id, env_extras=env_extras)
        return argv, env

    def _build_args(
        self,
        job_type: str,
        snapshot_id: str | None,
        params: dict[str, Any],
        policy: dict[str, Any],
    ) -> WhitelistArgs:
        """按任务类型构造白名单参数（方案 §6：无自由字符串）。

        注：``type=dump`` 任务在 :meth:`_run` 中于 restic 阶段之前终结
        （dump 即终态，见 :meth:`_finish_dump_only`），理论不会走到本方法；
        分支保留缺省白名单仅作完整性兜底（替换 R-019 之前的占位报错）。
        """
        if job_type == "dump":
            return WhitelistArgs()

        if job_type == "repair":
            # B1 自愈：restic repair index / repair packs。repair 不产生 JSON 输出，
            # 故 json_output=False；子命令（index/packs）缺省 index，经 ResticCmd 白名单校验。
            return WhitelistArgs(
                json_output=False,
                repair_target=str(params.get("repair_target") or "index"),
            )

        if job_type == "recover":
            # B3 自愈：restic recover 找回丢失索引的快照，无子命令、无额外 flag；
            # 输出为人类可读文本，json_output=False（与 repair 同口径）。
            return WhitelistArgs(json_output=False)

        builtin = self._exclusions.built_in()
        user_excludes = list(policy.get("excludes") or []) + list(params.get("excludes") or [])
        excludes = assemble_excludes(user_excludes, builtin)
        tags = tuple(params.get("tags") or ([policy["tag"]] if policy.get("tag") else []))

        if job_type == "backup":
            sources = list(params.get("sources") or policy.get("sources") or [])
            if not sources:
                raise ValidationError("备份任务未指定备份源（请在策略或请求中提供 sources）")
            # 批次②：exclude_spec 为唯一事实源时在此展平（--exclude/--iexclude/大小/
            # 标记文件/缓存）；否则回落到 excludes 列（旧任务，行为不变）。
            spec_raw = policy.get("exclude_spec")
            if spec_raw:
                flat = flatten_exclude_spec(
                    spec_from_wire(spec_raw), PathMapper(self._settings)
                )
                # A 裁决（owner 2026-10-24）：内置集排**队尾**——restic 后命中者赢，
                # 内置集因此真强制（负向规则取消不了）；顺序语义单一出处 = assemble_excludes。
                backup_excludes = assemble_excludes(
                    list(flat.excludes) + list(params.get("excludes") or []), builtin
                )
                iexcludes = flat.iexcludes
                larger_than = flat.larger_than
                if_present = flat.if_present
                caches = flat.caches
            else:
                backup_excludes = excludes
                iexcludes = ()
                larger_than = None
                if_present = ()
                caches = False
            return WhitelistArgs(
                targets=tuple(str(item) for item in sources),
                excludes=backup_excludes,
                iexcludes=iexcludes,
                exclude_larger_than=larger_than,
                exclude_if_present=if_present,
                exclude_caches=caches,
                tags=tags,
                # --host 取自 repos 表该仓库的 host_tag（仓库级配置，非任务列），由
                # TaskRepo.get_dict 透传；空值不加 flag。值仍走 WhitelistArgs.host
                # → _assert_safe_value 白名单校验，**不裸拼进 argv**。
                host=_clean_host(policy.get("host_tag")),
                # A0 模式收敛后 one_file_system 缺省统一为 True（方案 §3.2 四层防线第一层；
                # 原 host 模式 False 分支已随 host 模式删除）
                one_file_system=bool(policy.get("one_file_system", True)),
                compression=str(policy.get("compression") or "auto"),
                limit_up_kibps=int(policy.get("limit_up_kibps") or 0),
                limit_down_kibps=int(policy.get("limit_down_kibps") or 0),
            )

        if job_type == "restore":
            target = params.get("target")
            if not target:
                raise ValidationError("恢复任务缺少 target（P1 仅支持可写挂载点下的时间戳子目录）")
            return WhitelistArgs(
                snapshot_id=snapshot_id or params.get("snapshot_id"),
                target_dir=str(target),
                # R-015 原位恢复：目标为 INPLACE_RESTORE_TARGET_ROOT（"/"）时的显式放行
                # 开关（取值仍由 ResticCmd 决定，此处不透传路径字符串——S2）
                inplace_target=bool(params.get("inplace", False)),
                overwrite=str(params.get("overwrite") or "if-changed"),
                # B1 文件级恢复：容器形态的 include 清单（路由层已 display→container 归一，
                # 此处再过 ResticCmd 白名单校验——S1 双重校验点②）
                include=tuple(str(item) for item in params.get("include") or []),
            )

        if job_type == "check":
            # R-017：read_data（全量读数据）与 read_data_subset（抽样读数据）二选一，
            # 互斥与形态校验由 ResticCmd 白名单层兜底（WhitelistArgs 前移 422）。
            subset = params.get("read_data_subset")
            return WhitelistArgs(
                read_data=bool(params.get("read_data", False)),
                read_data_subset=str(subset).strip() if subset else None,
            )

        if job_type == "forget":
            # R-016：执行参数与 E21 预览同源（params 优先，回退 profile 字段）；
            # prune 只属于确认后的执行（预览恒 dry_run 且不带 --prune）。
            # 0 值口径：Task.keep_* 默认 0——0 = 规则未启用（与 --keep-last 0 的
            # 歧义 restic 语义切割），统一转 None 交 G3 兜底判定。
            keep = {
                "keep_last": _as_keep_rule(params.get("keep_last", policy.get("keep_last"))),
                "keep_hourly": _as_keep_rule(params.get("keep_hourly", policy.get("keep_hourly"))),
                "keep_daily": _as_keep_rule(params.get("keep_daily", policy.get("keep_daily"))),
                "keep_weekly": _as_keep_rule(params.get("keep_weekly", policy.get("keep_weekly"))),
                "keep_monthly": _as_keep_rule(params.get("keep_monthly", policy.get("keep_monthly"))),
                "keep_yearly": _as_keep_rule(params.get("keep_yearly", policy.get("keep_yearly"))),
                # A2 时长档（--keep-within*）：拼装层同款归一（形态校验前移；
                # 空/全零 = 未启用，与计数档 0 口径一致）
                "keep_within": _as_within_rule(
                    params.get("keep_within", policy.get("keep_within")), "keep_within"
                ),
                "keep_within_hourly": _as_within_rule(
                    params.get("keep_within_hourly", policy.get("keep_within_hourly")),
                    "keep_within_hourly",
                ),
                "keep_within_daily": _as_within_rule(
                    params.get("keep_within_daily", policy.get("keep_within_daily")),
                    "keep_within_daily",
                ),
                "keep_within_weekly": _as_within_rule(
                    params.get("keep_within_weekly", policy.get("keep_within_weekly")),
                    "keep_within_weekly",
                ),
                "keep_within_monthly": _as_within_rule(
                    params.get("keep_within_monthly", policy.get("keep_within_monthly")),
                    "keep_within_monthly",
                ),
                "keep_within_yearly": _as_within_rule(
                    params.get("keep_within_yearly", policy.get("keep_within_yearly")),
                    "keep_within_yearly",
                ),
            }
            # A2 标签档（--keep-tag）：空 tuple = 未启用
            keep_tags = _as_keep_tags(params.get("keep_tags", policy.get("keep_tags")))
            # G3 服务端兜底：无任何保留规则（计数/时长/标签三门并列，A2）的 forget
            # 会删除分组内全部快照——与 E21 预览同拒 422（routers/ops.py
            # require_keep_rule），杜绝绕过预览门直接提交「全删」语义。
            if all(value is None for value in keep.values()) and not keep_tags:
                raise ValidationError(
                    "forget 必须至少指定一个保留规则（keep_last/keep_hourly/…、keep_within/keep_tags），"
                    "否则会删除分组内全部快照",
                    data={"keep_fields": [*keep, "keep_tags"]},
                )
            return WhitelistArgs(
                tags=tags,
                group_by=str(params.get("group_by") or policy.get("group_by") or "host,paths,tags"),
                **keep,  # type: ignore[arg-type]
                keep_tags=keep_tags,
                prune=bool(params.get("prune", False)),
            )

        if job_type == "prune":
            # 独立回收：params 恒空（无 tag/group_by/keep 概念）；执行路径走 restic prune，
            # 由 _build_args 返回空 WhitelistArgs（dry_run=False → 真实 prune）。
            # 仅预览端点传 dry_run=True 走只读探测。
            return WhitelistArgs()

        return WhitelistArgs()

    # ---------------------------------------------------------------- 依赖读取

    async def list_repos(self) -> list[Repo]:
        """列出全部仓库（调度器维护校验遍历用；default 恒首位，见 ReposRepo.list_all）。"""
        return await self._repos.list_all()

    async def _repo_url(self, repo_id: str = DEFAULT_REPO_ID) -> str:
        """读取仓库地址（repos 表，R-023 后的事实源），未配置返回空串。"""
        repo = await self._repos.get(repo_id)
        return (repo.url if repo is not None else None) or ""

    async def _load_policy(self, policy_id: int | None) -> dict[str, Any]:
        """加载策略字典；无 loader 或策略缺失时返回空字典。"""
        if policy_id is None or self._policy_loader is None:
            return {}
        try:
            policy = await self._policy_loader(policy_id)
        except Exception as exc:  # noqa: BLE001 - 策略不可读不应中断任务执行
            logger.warning("加载策略 %s 失败：%s", policy_id, exc)
            return {}
        return policy or {}

    async def _load_task(self, task_id: int | None) -> dict[str, Any]:
        """加载任务字典；无 loader 或任务缺失时返回空字典（与 ``_load_policy`` 同构）。"""
        if task_id is None or self._task_loader is None:
            return {}
        try:
            task = await self._task_loader(task_id)
        except Exception as exc:  # noqa: BLE001 - 任务不可读不应中断任务执行
            logger.warning("加载任务 %s 失败：%s", task_id, exc)
            return {}
        return task or {}

    async def _load_profile(self, job: Job) -> dict[str, Any]:
        """profile 解析（增量设计 §7.1）：``task_id`` 优先，legacy ``policy_id`` 兼容。

        任务即策略（1:1）——新任务 job 只落 task_id；policy_id 仅为 legacy 行兼容读。
        """
        if job.task_id is not None:
            return await self._load_task(job.task_id)
        return await self._load_policy(job.policy_id)

    async def _task_name(self, task_id: int | None) -> str | None:
        """解析运行中 job 的任务名（E12 409 data 的 ``running_task_name``）。

        无任务关联（legacy 策略 job）或任务不可读时返回 None——UI 回落「已有任务进行中」。
        """
        if task_id is None or self._task_loader is None:
            return None
        try:
            profile = await self._task_loader(task_id)
        except Exception as exc:  # noqa: BLE001 - 名称解析失败不应影响冲突判定本身
            logger.warning("加载任务 %s 名称失败：%s", task_id, exc)
            return None
        if not profile:
            return None
        name = profile.get("name")
        return str(name) if name else None

    async def _load_credentials(self, repo_id: str = DEFAULT_REPO_ID) -> dict[str, str]:
        """把 config 中该仓库的 ``cred.<repo_id>.*`` 键还原为子进程环境变量。"""
        return await load_credentials(self._config, repo_id)

    @staticmethod
    def _summarize(status: str, aggregate: JobAggregate, exit_code: int) -> str:
        """生成任务摘要文案（落 jobs.summary）。"""
        return (
            f"{status}（exit={exit_code}）files={aggregate.files_done} "
            f"bytes={aggregate.bytes_done} skipped={aggregate.skipped_count}"
        )


def _retry_delay(attempt: int) -> float:
    """C3 指数退避：``base * 2 ** (attempt - 1)``，钳上限（attempt 从 1 计）。

    钳上限是必须的：没有上限的指数退避在 retry_count=5 时最后一次会等 8 分钟，
    任务挂在 running 里不释放仓库锁——把整条互斥语义拖坏（E1 回归面）。
    """
    return min(RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1)), RETRY_MAX_DELAY_SECONDS)


def _append_log_sync(log_path: Path, line: str) -> None:
    """同步追加一行到任务日志（重试痕迹走同一日志文件，SSE/E27 可见）。"""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(f"{line}\n")


def _as_optional_int(value: Any) -> int | None:
    """把策略字段安全地转为可选整数。"""
    return int(value) if isinstance(value, int | float) else None


def _as_keep_rule(value: Any) -> int | None:
    """forget 保留规则归一（R-016 0 值口径）：``>0`` 整数为规则；0/缺失/非法 = 未启用。

    ``Task.keep_*`` 默认 0（backup_tasks DDL 缺省），若照原值传 restic 会产生
    ``--keep-last 0`` 的歧义语义（该桶保留 0 个＝全删），故 0 一律视为未配置，
    交由 G3「至少一个规则」兜底判定拒绝或忽略该维度。
    """
    converted = _as_optional_int(value)
    return converted if converted is not None and converted > 0 else None


def _as_within_rule(value: Any, field_name: str) -> str | None:
    """forget 时长档归一（A2）：形态校验 + 空/全零 = 未启用（None）。

    复用 restic 层 :func:`_normalize_duration`（单一校验实现，与 argv 拼装层
    同源）：params 来自 E21 回显（预览时已归一）或任务 profile（写侧已校验），
    此处仍防御性校验——绕过前端的直提交若带坏 duration，restic 端是 Fatal 且
    零 JSON 产出，执行层没有兜底机会（§7.1 V4 事实 3 同型）。
    """
    return _normalize_duration(str(value) if value is not None else None, field_name)


def _as_mirror_ids(value: Any) -> tuple[str, ...]:
    """C4 镜像仓 id 归一：JSON 文本（DB 形态）或 list（API 形态）→ 保序去重 tuple。

    数量钳 :data:`~app.constants.MIRROR_REPO_IDS_MAX` 在此**再兜一次**：写侧
    （TaskService）已钳过，此处防的是历史脏数据与直改库的行——运行期拿到超限清单
    不能照单全跑（每多一个镜像就多一次全链路 copy）。
    """
    if isinstance(value, str):
        try:
            loaded = json.loads(value)
        except (TypeError, ValueError):
            logger.warning("mirror_repo_ids 非合法 JSON，按未配置处理")
            return ()
        items = loaded if isinstance(loaded, list) else []
    elif isinstance(value, list | tuple):
        items = list(value)
    else:
        return ()
    result: list[str] = []
    for raw in items:
        item = str(raw).strip()
        if item and item not in result:
            result.append(item)
    return tuple(result[:MIRROR_REPO_IDS_MAX])


def _mirror_note(outcomes: list[MirrorOutcome]) -> str:
    """C4 镜像结果摘要（逐个带原因，禁止只说「有镜像失败」）。

    只记真失败：跳过项（如镜像仓就是源仓）是配置口径，不该混进失败提示。
    """
    failed = [item for item in outcomes if not item.ok and not item.skipped]
    if not failed:
        return ""
    detail = "、".join(f"{item.repo_id}（{item.reason}）" for item in failed)
    return f"；镜像同步失败：{detail}"


def _as_keep_tags(value: Any) -> tuple[str, ...]:
    """forget 标签档归一（A2 ``--keep-tag``）：去空白、丢空、保序去重；空 tuple = 未启用。

    接受 list/tuple（API 契约形态）或单字符串（宽松兼容）；flag 注入校验由
    拼装层 :func:`_assert_safe_value` 终判（与 ``--tag`` 同款）。
    """
    if value is None:
        return ()
    candidates: list[Any] = list(value) if isinstance(value, list | tuple) else [value]
    result: list[str] = []
    seen: set[str] = set()
    for raw in candidates:
        item = str(raw).strip()
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return tuple(result)


def _clean_host(value: Any) -> str | None:
    """把仓库的 ``host_tag``（repos 表列）归一为可安全传给 ``--host`` 的值。

    Args:
        value: 配置值（可能为 None / 空串 / 非字符串）。

    Returns:
        去空白后的主机名；空值返回 ``None``（表示不加 ``--host`` flag）。
    """
    if value is None:
        return None
    text = str(value).strip()
    return text or None


