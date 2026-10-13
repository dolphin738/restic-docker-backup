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
import logging
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from app.constants import (
    ACTIVE_JOB_STATUSES,
    AUDIT_JOB_CANCEL,
    AUDIT_JOB_FINISHED,
    AUDIT_JOB_RECOVERED,
    AUDIT_JOB_SKIPPED_CONFLICT,
    AUDIT_JOB_SUBMIT,
    JOB_TYPES,
)
from app.db.database import Database
from app.db.repositories.audit_repo import AuditRepo
from app.db.repositories.config_repo import ConfigRepo
from app.db.repositories.job_repo import Job, JobRepo
from app.errors import JobConflictError, NotCancellableError, NotFoundError, ValidationError
from app.exclusions import ExclusionRules
from app.services.env_sync import load_credentials
from app.services.parser import JobAggregate, OutputParser, ProgressEvent, SnapshotRecord
from app.services.restic import ResticCmd, ResticProcess, WhitelistArgs
from app.settings import Settings, get_settings
from app.utils.time import utc_iso
from app.utils.ulid import new_job_id

logger = logging.getLogger(__name__)

PolicyLoader = Callable[[int], Awaitable[dict[str, Any] | None]]
# task_loader 与 policy_loader 同构契约（(int) -> Awaitable[dict | None]）：
# TaskRepo.get_dict / PolicyRepo.get_dict 均按此签名注入（增量设计 §1.1 仿先例）。
TaskLoader = PolicyLoader


class NoopNotifier:
    """通知模块占位实现（P1 空转，不产生任何副作用）。

    TODO(P2): 通知模块（healthchecks / webhook / MeoW + 渠道级脱敏）实现后由真实 Notifier 替换注入，
    届时本类可整体删除（JobRunner 侧无需改动，仅改注入）。
    """

    async def notify(self, *, job_type: str, status: str, detail: dict[str, Any] | None = None) -> None:
        """空实现：仅记录 debug 日志，便于排障确认调用点已接通。"""
        logger.debug("NoopNotifier 收到通知（P2 才真正发送）：type=%s status=%s", job_type, status)


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
        self._exclusions = ExclusionRules(self._settings)
        self._locks: dict[str, asyncio.Lock] = {}
        self._processes: dict[str, ResticProcess] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._params: dict[str, dict[str, Any]] = {}

    # ---------------------------------------------------------------- 互斥锁（§7.1）

    def _repo_lock(self, repo_url: str) -> asyncio.Lock:
        """按仓库维度惰性建锁（当前单仓库；R-023 多仓库时按 repo_url 分桶）。"""
        lock = self._locks.get(repo_url)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[repo_url] = lock
        return lock

    # ---------------------------------------------------------------- 提交与取消

    async def submit(
        self,
        job_type: str,
        *,
        policy_id: int | None = None,
        task_id: int | None = None,
        trigger: str = "manual",
        snapshot_id: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> Job:
        """提交任务：查重 → 落 pending → 起后台执行任务。

        Args:
            job_type: 任务类型（E12 白名单）。
            policy_id: 关联策略 id（legacy）；可为空。
            task_id: 关联备份任务 id（选项②；与 policy_id 互斥由上层校验）；
                新任务 job 落 ``task_id``、``policy_id`` 为 NULL（§1.2 决策二）。
            trigger: ``manual`` / ``scheduled``。
            snapshot_id: 关联快照（restore/forget）。
            params: 补充参数（sources/excludes/target/include…）。

        Returns:
            新建任务（manual 冲突时抛错；scheduled 冲突时返回一条 cancelled 任务）。

        Raises:
            JobConflictError: manual 触发且已有 running/pending 任务（409，
                data 带 ``running_job_id`` 与 ``running_task_name``）。
        """
        if job_type not in JOB_TYPES:
            raise ValidationError(f"不支持的任务类型：{job_type}", data={"allowed": list(JOB_TYPES)})
        repo_url = await self._repo_url()
        lock = self._repo_lock(repo_url)

        async with lock:
            active = await self._jobs.list_active()
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
                policy_id=policy_id,
                task_id=task_id,
                snapshot_id=snapshot_id,
            )

        self._params[job_id] = dict(params or {})
        await self._audit.record(
            AUDIT_JOB_SUBMIT,
            target=job_id,
            detail={"type": job_type, "trigger": trigger, "policy_id": policy_id, "task_id": task_id},
            result="ok",
        )
        self._tasks[job_id] = asyncio.create_task(self._run(job), name=f"job-{job_id}")
        return job

    async def cancel(self, job_id: str) -> Job:
        """取消任务：SIGTERM → 宽限 grace_seconds → SIGKILL，终态 cancelled。

        Args:
            job_id: 任务 id。

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

        await self._jobs.finalize(job_id, "cancelled", exit_code=-1, summary="用户取消")
        await self._audit.record(AUDIT_JOB_CANCEL, target=job_id, detail={"type": job.type}, result="ok")
        updated = await self._jobs.get(job_id)
        return updated or job

    # ---------------------------------------------------------------- 执行主链路（§6.2）

    async def _run(self, job: Job) -> None:
        """执行单个任务：抢锁 → running → spawn → 逐行解析落库 → 终态 → 审计/通知。"""
        repo_url = await self._repo_url()
        async with self._repo_lock(repo_url):
            line_no = 0
            try:
                await self._jobs.update_status(job.id, "running", started_at=utc_iso())
                argv, env = await self._prepare(job, repo_url)
                log_path = Path(job.log_path)
                proc = await self._restic.spawn(argv, log_path, env=env)
                self._processes[job.id] = proc

                aggregate = JobAggregate()
                snapshot_id = job.snapshot_id
                last_flush = time.monotonic()

                async for line in proc.lines():
                    line_no += 1
                    event = await asyncio.to_thread(self._parser.parse_line, line)
                    if isinstance(event, ProgressEvent):
                        event.event_id = line_no
                        aggregate = self._parser.aggregate(aggregate, event)
                    elif isinstance(event, SnapshotRecord) and event.snapshot_id:
                        snapshot_id = event.snapshot_id
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
                status = "success" if exit_code == 0 else "failed"
                await self._jobs.finalize(
                    job.id,
                    status,
                    exit_code=exit_code,
                    summary=self._summarize(status, aggregate, exit_code),
                    bytes_done=aggregate.bytes_done,
                    files_done=aggregate.files_done,
                    skipped_count=aggregate.skipped_count,
                    last_event_id=line_no,
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
                    },
                    result="ok" if status == "success" else "failed",
                )
                await self._notifier.notify(
                    job_type=job.type,
                    status=status,
                    detail={"job_id": job.id, "skipped_count": aggregate.skipped_count},
                )
                if status == "success" and job.type == "backup" and job.task_id is not None:
                    # X6（owner 裁决）：「E36 预览成功**或首次成功备份**」清 dry_run_pending
                    # 的后半——绕过 UI 直接成功备份同样解除 Start 拦截（R-026-08）
                    await self._notify_task_backup_success(job.task_id)
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
            finally:
                self._processes.pop(job.id, None)
                self._params.pop(job.id, None)
                self._tasks.pop(job.id, None)
                self._notify_finish()

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
        # TODO(P2): 按策略补跑（调度器就绪后）+ AUTO_UNLOCK 语义处理仓库锁残留（§6.5）
        return affected

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

    async def _prepare(self, job: Job, repo_url: str) -> tuple[list[str], dict[str, str]]:
        """拼装 argv 与子进程环境。"""
        params = self._params.get(job.id, {})
        profile = await self._load_profile(job)
        args = self._build_args(job.type, job.snapshot_id, params, profile)
        env_extras = await self._load_credentials()
        argv = self._restic.build(job.type, args=args, env_extras=env_extras)
        env = self._restic.build_env(repo_url=repo_url, env_extras=env_extras)
        return argv, env

    def _build_args(
        self,
        job_type: str,
        snapshot_id: str | None,
        params: dict[str, Any],
        policy: dict[str, Any],
    ) -> WhitelistArgs:
        """按任务类型构造白名单参数（方案 §6：无自由字符串）。"""
        if job_type == "dump":
            raise ValidationError("dump 任务随 P2 Dump 模块生效，当前不可用", data={"type": job_type})

        builtin = self._exclusions.built_in()
        user_excludes = list(policy.get("excludes") or []) + list(params.get("excludes") or [])
        excludes = tuple(_dedupe(builtin + user_excludes))
        tags = tuple(params.get("tags") or ([policy["tag"]] if policy.get("tag") else []))

        if job_type == "backup":
            sources = list(params.get("sources") or policy.get("sources") or [])
            if not sources:
                raise ValidationError("备份任务未指定备份源（请在策略或请求中提供 sources）")
            return WhitelistArgs(
                targets=tuple(str(item) for item in sources),
                excludes=excludes,
                tags=tags,
                # --host 取自 config.repo.host_tag（仓库级配置，非策略列），由
                # PolicyRepo.get_dict 透传；空值不加 flag。值仍走 WhitelistArgs.host
                # → _assert_safe_value 白名单校验，**不裸拼进 argv**。
                host=_clean_host(policy.get("host_tag")),
                one_file_system=bool(policy.get("one_file_system", self._settings.restic_web_mode == "sources")),
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
                overwrite=str(params.get("overwrite") or "if-changed"),
                # B1 文件级恢复：容器形态的 include 清单（路由层已 display→container 归一，
                # 此处再过 ResticCmd 白名单校验——S1 双重校验点②）
                include=tuple(str(item) for item in params.get("include") or []),
            )

        if job_type == "check":
            return WhitelistArgs(read_data=bool(params.get("read_data", False)))

        if job_type == "forget":
            return WhitelistArgs(
                tags=tags,
                group_by=str(params.get("group_by") or policy.get("group_by") or "host,paths,tags"),
                keep_last=_as_optional_int(params.get("keep_last", policy.get("keep_last"))),
                keep_hourly=_as_optional_int(params.get("keep_hourly", policy.get("keep_hourly"))),
                keep_daily=_as_optional_int(params.get("keep_daily", policy.get("keep_daily"))),
                keep_weekly=_as_optional_int(params.get("keep_weekly", policy.get("keep_weekly"))),
                keep_monthly=_as_optional_int(params.get("keep_monthly", policy.get("keep_monthly"))),
                keep_yearly=_as_optional_int(params.get("keep_yearly", policy.get("keep_yearly"))),
            )

        return WhitelistArgs()

    # ---------------------------------------------------------------- 依赖读取

    async def _repo_url(self) -> str:
        """读取仓库地址（config.repo.url），未配置返回空串。"""
        return (await self._config.get_value("repo.url")) or ""

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

    async def _load_credentials(self) -> dict[str, str]:
        """把 config 中 ``cred.*`` 键还原为子进程环境变量（``AWS_*`` / ``B2_*``）。"""
        return await load_credentials(self._config)

    @staticmethod
    def _summarize(status: str, aggregate: JobAggregate, exit_code: int) -> str:
        """生成任务摘要文案（落 jobs.summary）。"""
        return (
            f"{status}（exit={exit_code}）files={aggregate.files_done} "
            f"bytes={aggregate.bytes_done} skipped={aggregate.skipped_count}"
        )


def _as_optional_int(value: Any) -> int | None:
    """把策略字段安全地转为可选整数。"""
    return int(value) if isinstance(value, int | float) else None


def _clean_host(value: Any) -> str | None:
    """把 ``config.repo.host_tag`` 归一为可安全传给 ``--host`` 的值。

    Args:
        value: 配置值（可能为 None / 空串 / 非字符串）。

    Returns:
        去空白后的主机名；空值返回 ``None``（表示不加 ``--host`` flag）。
    """
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _dedupe(values: list[str]) -> list[str]:
    """按字符串去重并保持顺序。"""
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result
