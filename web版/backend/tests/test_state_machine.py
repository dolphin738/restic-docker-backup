"""JobRunner 六状态机测试（§10.1 状态机层）：表驱动转换、冲突拒绝、启动恢复、取消。

断言基线（主理人裁决）：recover_on_startup 把遗留 running **与 pending** 一并置 interrupted。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.constants import (
    ACTIVE_JOB_STATUSES,
    BEHAVIOR_AUTO_INIT_KEY,
    BEHAVIOR_AUTO_UNLOCK_KEY,
    JOB_STATUSES,
    TERMINAL_JOB_STATUSES,
)
from app.db.database import Database
from app.db.repositories.audit_repo import AuditRepo
from app.db.repositories.config_repo import ConfigRepo
from app.db.repositories.job_repo import Job, JobRepo
from app.db.repositories.task_repo import TaskRepo
from app.errors import (
    JobConflictError,
    NotCancellableError,
    NotFoundError,
    ValidationError,
)
from app.services.job_runner import JobRunner
from app.services.parser import OutputParser
from app.settings import Settings

BACKUP_LINES = [
    '{"message_type":"status","percent_done":0.0,"total_files":2,"files_done":0,"total_bytes":100,"bytes_done":0}',
    '{"message_type":"status","percent_done":1.0,"total_files":2,"files_done":2,"total_bytes":100,"bytes_done":100}',
    '{"message_type":"error","during":"archival","item":"/sources/etc/shadow","error":"permission denied"}',
    '{"message_type":"summary","total_files_processed":2,"total_bytes_processed":100,'
    '"snapshot_id":"a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2",'
    '"snapshot_time":"2026-10-10T01:38:00Z"}',
]

# X12：restic restore 的 --json 完成摘要（真机取证形态，架构文档 §12 V7）——
# --overwrite=always 下仍有 3 个文件因权限写不进去 → files_skipped:3。旧实现丢弃
# 该字段导致 job.skipped_count 恒 0、任务静默报 success。
RESTORE_LINES = [
    '{"message_type":"status","percent_done":1.0,"total_files":3,"files_done":3,"total_bytes":20,"bytes_done":0}',
    '{"message_type":"summary","total_files":2,"files_restored":3,"files_skipped":3,"bytes_skipped":20}',
]


@dataclass
class FakeProcess:
    """ResticProcess 替身：lines() 同步 tee 写日志文件（与真实实现一致的行序保证）。"""

    out_lines: list[str]
    log_path: Path
    exit_code: int = 0
    terminated: bool = field(default=False)
    killed: bool = field(default=False)

    async def lines(self):  # noqa: ANN201
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("ab") as sink:
            for line in self.out_lines:
                sink.write((line + "\n").encode("utf-8"))
                sink.flush()
                yield line

    async def wait(self) -> int:
        return self.exit_code

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True


class FakeRestic:
    """ResticCmd 替身：build/build_env/spawn 全部离线。"""

    def __init__(
        self,
        lines: list[str] | None = None,
        exit_code: int = 0,
        spawn_error: Exception | None = None,
    ) -> None:
        self.lines = list(lines or [])
        self.exit_code = exit_code
        self.spawn_error = spawn_error
        self.last_process: FakeProcess | None = None
        self.last_args = None  # 最近一次 build 的 WhitelistArgs（profile 透传断言用）

    def build(self, verb, *, args, env_extras=None):  # noqa: ANN001, ANN202
        self.last_args = args
        return ["restic-fake", verb]

    def build_env(self, *, repo_url, repo_id="default", env_extras=None):  # noqa: ANN001, ANN202
        return {}

    async def spawn(self, argv, log_path, *, env=None):  # noqa: ANN001, ANN202
        if self.spawn_error is not None:
            raise self.spawn_error
        self.last_process = FakeProcess(self.lines, Path(log_path), self.exit_code)
        return self.last_process


async def make_runner(tmp_path, **fake_kwargs) -> tuple[Database, JobRunner, JobRepo]:
    db = Database(tmp_path / "jobs.db")
    await db.connect()
    settings = Settings(data_dir=str(tmp_path))
    settings.jobs_dir.mkdir(parents=True, exist_ok=True)
    runner = JobRunner(
        db,
        FakeRestic(**fake_kwargs),  # type: ignore[arg-type]
        OutputParser(),
        AuditRepo(db),
        settings=settings,
    )
    return db, runner, JobRepo(db)


async def make_runner_with_tasks(
    tmp_path, *, on_task_backup_success=None, **fake_kwargs
) -> tuple[Database, JobRunner, JobRepo, TaskRepo]:
    """带 task_loader 的 runner（选项②：profile 解析走 TaskRepo.get_dict 注入）。

    ``on_task_backup_success``：X6 回调注入（缺省 None，与生产装配的可选语义一致）。
    """
    db = Database(tmp_path / "jobs.db")
    await db.connect()
    settings = Settings(data_dir=str(tmp_path))
    settings.jobs_dir.mkdir(parents=True, exist_ok=True)
    task_repo = TaskRepo(db)
    runner = JobRunner(
        db,
        FakeRestic(**fake_kwargs),  # type: ignore[arg-type]
        OutputParser(),
        AuditRepo(db),
        settings=settings,
        task_loader=task_repo.get_dict,
        on_task_backup_success=on_task_backup_success,
    )
    return db, runner, JobRepo(db), task_repo


async def await_job(runner: JobRunner, job_id: str) -> None:
    """等待 submit 派生的后台执行任务结束（保证确定性）。"""
    task = runner._tasks.get(job_id)
    if task is not None:
        await task


async def insert_job(
    repo: JobRepo,
    job_id: str,
    status: str,
    *,
    trigger: str = "manual",
    task_id: int | None = None,
) -> None:
    await repo.create(
        job_id=job_id,
        job_type="backup",
        trigger=trigger,
        log_path=f"/tmp/{job_id}.log",
        status=status,
        task_id=task_id,
    )


# ---------------------------------------------------------------- 状态常量（§4.5 状态机）

def test_job_status_vocabulary_is_exactly_six():
    """状态词表恰为六态，且终态是 success（不是 succeeded）。"""
    assert JOB_STATUSES == ("pending", "running", "success", "failed", "interrupted", "cancelled")
    assert "succeeded" not in JOB_STATUSES
    assert set(ACTIVE_JOB_STATUSES) == {"running", "pending"}
    assert set(TERMINAL_JOB_STATUSES) == {"success", "failed", "interrupted", "cancelled"}


# ---------------------------------------------------------------- 转换：pending → running → success

async def test_pending_running_success(tmp_path):
    db, runner, repo = await make_runner(tmp_path, lines=BACKUP_LINES)

    job = await runner.submit("backup", params={"sources": ["/sources/etc"]})
    assert job.status == "pending"

    await await_job(runner, job.id)
    final = await repo.get(job.id)
    assert final is not None
    assert final.status == "success"
    assert final.exit_code == 0
    assert final.started_at is not None
    assert final.finished_at is not None
    assert final.bytes_done == 100  # status 快照式取 max
    assert final.files_done == 2
    assert final.skipped_count == 1  # archival error
    assert final.last_event_id == 4  # 日志行号推进到最后一行
    assert final.snapshot_id == "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2"
    await db.close()


async def test_restore_summary_files_skipped_folds_into_job(tmp_path):
    """X12 回归：restore summary 的 files_skipped 折入 job.skipped_count（此前恒 0）。

    真机取证形态（架构文档 §12 V7）：非 root 容器原位恢复，``--overwrite=always``
    下仍有 3 个文件因权限写不进去，restic 摘要 ``files_skipped:3``；旧实现丢弃该
    字段 → ``skipped_count=0``、任务静默报 success（静默部分失败）。本测试锁定
    修复：``skipped_count`` 反映真实跳过数，且终态仍为 ``success``（匹配 restic
    exit_code=0；不引入第七状态值，遵守六态词表）。
    """
    db, runner, repo = await make_runner(tmp_path, lines=RESTORE_LINES)

    job = await runner.submit(
        "restore",
        snapshot_id="a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2",
        params={"target": "/", "inplace": True, "overwrite": "always", "include": ["/sources/perm2"]},
    )

    await await_job(runner, job.id)
    final = await repo.get(job.id)
    assert final is not None
    assert final.status == "success"
    assert final.exit_code == 0
    assert final.skipped_count == 3  # X12：restore summary files_skipped 不再被丢弃
    await db.close()


async def test_nonzero_exit_maps_to_failed(tmp_path):
    db, runner, repo = await make_runner(tmp_path, lines=BACKUP_LINES, exit_code=3)

    job = await runner.submit("backup", params={"sources": ["/sources/etc"]})
    await await_job(runner, job.id)

    final = await repo.get(job.id)
    assert final.status == "failed"
    assert final.exit_code == 3
    await db.close()


async def test_spawn_error_converges_to_failed(tmp_path):
    """spawn 抛异常等全部异常路径收敛为 failed，不泄漏内部细节。"""
    db, runner, repo = await make_runner(
        tmp_path, spawn_error=FileNotFoundError("restic binary missing")
    )

    job = await runner.submit("backup", params={"sources": ["/sources/etc"]})
    await await_job(runner, job.id)

    final = await repo.get(job.id)
    assert final.status == "failed"
    assert final.exit_code == -1
    assert "FileNotFoundError" in (final.summary or "")
    await db.close()


async def test_scheduled_trigger_runs_normally_when_free(tmp_path):
    db, runner, repo = await make_runner(tmp_path, lines=BACKUP_LINES)
    job = await runner.submit("backup", trigger="scheduled", params={"sources": ["/sources/etc"]})
    await await_job(runner, job.id)
    final = await repo.get(job.id)
    assert final.status == "success"
    assert final.trigger == "scheduled"
    await db.close()


# ---------------------------------------------------------------- 冲突：manual 409 / scheduled 落 cancelled 跳过

async def test_manual_submit_conflict_raises_409(tmp_path):
    db, runner, repo = await make_runner(tmp_path, lines=BACKUP_LINES)
    await insert_job(repo, "existing", "running")

    with pytest.raises(JobConflictError) as excinfo:
        await runner.submit("backup", params={"sources": ["/sources/etc"]})
    # E12 契约（选项②）：data 增 running_task_name（运行中 job 无任务关联时为 None）
    assert excinfo.value.data == {"running_job_id": "existing", "running_task_name": None}
    await db.close()


async def test_manual_conflict_also_triggers_on_pending(tmp_path):
    db, runner, repo = await make_runner(tmp_path)
    await insert_job(repo, "queued", "pending")
    with pytest.raises(JobConflictError):
        await runner.submit("check")
    await db.close()


async def test_conflict_answered_without_waiting_for_repo_lock(tmp_path):
    """仓库锁被运行中任务持有时，提交必须**立刻** 409，而不是阻塞到任务结束。

    真机取证 2026-10-25：``_run`` 按 E1 整段持有仓库锁，旧 ``submit`` 直接
    ``async with lock`` → 任务运行期间点「开始」会静默挂住，直到任务结束才突然
    开始备份（既没有 409，也没有任何提示）。修复：提交前先无锁查活跃任务，命中
    即走冲突快路径（锁内仍复核一次，判定权威性不变）。
    """
    db, runner, repo = await make_runner(tmp_path, lines=BACKUP_LINES)
    await insert_job(repo, "long-running", "running")

    repo_url = await runner._repo_url("default")  # noqa: SLF001 - 测试取证内部状态
    async with runner._repo_lock(repo_url):  # noqa: SLF001 - 模拟运行中任务占锁
        # 2s 超时：旧实现阻塞到锁释放（抛出 TimeoutError，用例失败）
        with pytest.raises(JobConflictError):
            await asyncio.wait_for(
                runner.submit("backup", params={"sources": ["/sources/etc"]}), timeout=2
            )
    await db.close()


async def test_scheduled_conflict_records_cancelled_job_and_audit(tmp_path):
    """scheduled 冲突不 409：落一条 cancelled 任务 + 审计后跳过（§5.1 E12 注）。"""
    db, runner, repo = await make_runner(tmp_path)
    audit = AuditRepo(db)
    runner._audit = audit
    await insert_job(repo, "active", "running")

    skipped = await runner.submit("backup", trigger="scheduled", params={"sources": ["/sources/etc"]})
    assert skipped.status == "cancelled"

    rows, total = await audit.query(action="job.skipped_conflict")
    assert total == 1
    assert rows[0].target == skipped.id
    await db.close()


async def test_invalid_job_type_rejected(tmp_path):
    db, runner, _ = await make_runner(tmp_path)
    with pytest.raises(ValidationError):
        await runner.submit("bogus-type")
    await db.close()


# ---------------------------------------------------------------- 取消

async def test_cancel_running_job(tmp_path):
    db, runner, repo = await make_runner(tmp_path)
    await insert_job(repo, "job-run", "running")

    fake = FakeProcess([], Path(tmp_path / "job-run.log"))
    runner._processes["job-run"] = fake

    cancelled = await runner.cancel("job-run")
    assert cancelled.status == "cancelled"
    assert cancelled.exit_code == -1
    assert fake.terminated is True

    final = await repo.get("job-run")
    assert final.status == "cancelled"
    await db.close()


async def test_cancel_terminal_job_returns_409(tmp_path):
    db, runner, repo = await make_runner(tmp_path)
    for status in ("success", "failed", "interrupted", "cancelled"):
        await insert_job(repo, f"done-{status}", status)
        with pytest.raises(NotCancellableError):
            await runner.cancel(f"done-{status}")
    await db.close()


async def test_cancel_missing_job_returns_404(tmp_path):
    db, runner, _ = await make_runner(tmp_path)
    with pytest.raises(NotFoundError):
        await runner.cancel("no-such-job")
    await db.close()


# --------------------------------- 启动恢复（主理人裁决：running 与 pending 一并 interrupted）

async def test_recover_marks_running_and_pending_interrupted(tmp_path):
    db, runner, repo = await make_runner(tmp_path)
    await insert_job(repo, "j-running", "running")
    await insert_job(repo, "j-pending", "pending")
    await insert_job(repo, "j-success", "success")
    await insert_job(repo, "j-failed", "failed")

    affected = await runner.recover_on_startup()
    assert affected == 2

    assert (await repo.get("j-running")).status == "interrupted"
    assert (await repo.get("j-pending")).status == "interrupted"
    assert (await repo.get("j-success")).status == "success"
    assert (await repo.get("j-failed")).status == "failed"
    await db.close()


async def test_recover_is_noop_when_clean(tmp_path):
    db, runner, _ = await make_runner(tmp_path)
    assert await runner.recover_on_startup() == 0
    await db.close()


async def test_recover_writes_audit(tmp_path):
    db, runner, repo = await make_runner(tmp_path)
    await insert_job(repo, "j-run", "running")
    await runner.recover_on_startup()
    audit = AuditRepo(db)
    rows, total = await audit.query(action="job.recovered")
    assert total == 1
    await db.close()


# --------------------------------- 启动恢复 AUTO 分支（Q8 基线，批次 B：AUTO_UNLOCK / AUTO_INIT）

class FakeResticWithProbe(FakeRestic):
    """带 run_json 的替身：AUTO_INIT 的仓库初始化探测（cat config）落定结果。"""

    def __init__(self, *, probe_exit: int = 0, probe_lines: list[str] | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.probe_exit = probe_exit
        self.probe_lines = list(probe_lines or [])
        self.probe_calls = 0

    async def run_json(self, argv, *, env, timeout_seconds, max_lines=200_000):  # noqa: ANN001, ANN202
        self.probe_calls += 1
        from types import SimpleNamespace

        return SimpleNamespace(exit_code=self.probe_exit, lines=list(self.probe_lines), truncated=False)


async def _make_auto_runner(tmp_path, fake: FakeRestic):
    """带 config 仓储的 runner（AUTO 分支：behavior.* 开关与 repo.url 经 config 落库）。"""
    db = Database(tmp_path / "auto.db")
    await db.connect()
    settings = Settings(data_dir=str(tmp_path))
    settings.jobs_dir.mkdir(parents=True, exist_ok=True)
    runner = JobRunner(
        db,
        fake,  # type: ignore[arg-type]
        OutputParser(),
        AuditRepo(db),
        settings=settings,
    )
    return db, runner, JobRepo(db), ConfigRepo(db)


async def _submitted_types(repo: JobRepo) -> list[str]:
    jobs, _ = await repo.list_page()
    return [job.type for job in jobs]


async def test_auto_flags_off_keeps_behavior_identical(tmp_path):
    """开关缺省 off（回归保护）：recover 不产生任何 unlock/init 任务，行为与旧实现一致。"""
    fake = FakeResticWithProbe()
    db, runner, repo, config = await _make_auto_runner(tmp_path, fake)
    await config.set("repo.url", "/repo")
    await insert_job(repo, "j-run", "running")

    await runner.recover_on_startup()

    assert await _submitted_types(repo) == ["backup"]  # 仅遗留 interrupted 标记，无新任务
    assert fake.probe_calls == 0  # off 时连探测都不发生
    await db.close()


async def test_auto_unlock_submits_unlock_job(tmp_path):
    """AUTO_UNLOCK=1：提交 scheduled unlock 清理锁残留（审计沿用 job.submit/finished 埋点）。"""
    fake = FakeResticWithProbe(lines=[])
    db, runner, repo, config = await _make_auto_runner(tmp_path, fake)
    await runner._repos.update("default", url="/repo")
    await config.set(BEHAVIOR_AUTO_UNLOCK_KEY, "1")

    await runner.recover_on_startup()

    assert "unlock" in await _submitted_types(repo)
    await db.close()


async def test_auto_unlock_skips_when_repo_unconfigured(tmp_path):
    """仓库未配置：开关开着也不提交（避免无谓 failed 任务）。"""
    fake = FakeResticWithProbe()
    db, runner, repo, config = await _make_auto_runner(tmp_path, fake)
    await config.set(BEHAVIOR_AUTO_UNLOCK_KEY, "1")

    await runner.recover_on_startup()

    assert await _submitted_types(repo) == []
    await db.close()


async def test_auto_init_runs_when_repo_uninitialized(tmp_path):
    """AUTO_INIT=1 且探测命中「未初始化」→ 提交 init。"""
    fake = FakeResticWithProbe(
        probe_exit=1,
        probe_lines=['{"error":"unable to open config file"}'],
    )
    db, runner, repo, config = await _make_auto_runner(tmp_path, fake)
    await runner._repos.update("default", url="/repo")
    await config.set(BEHAVIOR_AUTO_INIT_KEY, "1")

    await runner.recover_on_startup()

    assert "init" in await _submitted_types(repo)
    await db.close()


async def test_auto_init_skips_when_initialized(tmp_path):
    """AUTO_INIT=1 但探测成功（已初始化）→ 不 init。"""
    fake = FakeResticWithProbe(probe_exit=0)
    db, runner, repo, config = await _make_auto_runner(tmp_path, fake)
    await config.set("repo.url", "/repo")
    await config.set(BEHAVIOR_AUTO_INIT_KEY, "1")

    await runner.recover_on_startup()

    assert await _submitted_types(repo) == []
    await db.close()


async def test_auto_init_skips_when_probe_inconclusive(tmp_path):
    """探测失败但非「未初始化」（如锁残留）→ 不动（fail-safe，绝不误 init）。"""
    fake = FakeResticWithProbe(
        probe_exit=1,
        probe_lines=["repository is already locked"],
    )
    db, runner, repo, config = await _make_auto_runner(tmp_path, fake)
    await config.set("repo.url", "/repo")
    await config.set(BEHAVIOR_AUTO_INIT_KEY, "1")

    await runner.recover_on_startup()

    assert await _submitted_types(repo) == []
    await db.close()


# ---------------------------------------------------------------- 关闭收尾

async def test_on_shutdown_terminates_processes(tmp_path):
    db, runner, _ = await make_runner(tmp_path)
    fake = FakeProcess([], Path(tmp_path / "x.log"))
    runner._processes["job-x"] = fake
    await runner.on_shutdown()
    assert fake.terminated is True
    assert runner._processes == {}
    await db.close()


async def test_submit_run_and_cancel_race_is_safe(tmp_path):
    """执行完成后 cancel 同一任务 → 409（终态不可取消），无悬挂任务。"""
    db, runner, repo = await make_runner(tmp_path, lines=BACKUP_LINES)
    job = await runner.submit("backup", params={"sources": ["/sources/etc"]})
    await await_job(runner, job.id)
    with pytest.raises(NotCancellableError):
        await runner.cancel(job.id)
    assert runner._tasks == {}
    await db.close()


# ---------------------------------------------------------------- 并发互斥（G6 真并发）

# 备注：本节用例在本机 Windows 可稳定通过（2026-10-11 三轮全量 117 passed）。
# 历史记录的「aiosqlite 用例挂起」经复盘为残留 python 进程所致，与本节无关——
# 如再遇疑似挂起，先排查残留进程（tasklist | findstr python）再下结论。


async def test_concurrent_manual_submit_exactly_one_wins(tmp_path):
    """真并发：两个 manual submit 同时发起 → 恰好 1 成功、1 个 JOB_CONFLICT。

    与 ``test_api/test_jobs.py`` 中「预置 running 再触发」的 409 测试不同：
    那里第二条请求在第一条已落库之后才开始，未真正重叠，
    因此无法证伪"检查与写入之间存在竞态窗口"这类缺陷。

    这里用 ``asyncio.gather`` 让两个协程在同一事件循环轮次内同时进入
    ``submit``，真正并发竞争 ``_repo_lock``（C1/C3）。
    """
    db, runner, repo = await make_runner(tmp_path, lines=BACKUP_LINES)

    async def submit_once() -> Job | BaseException:
        try:
            return await runner.submit("backup", params={"sources": ["/sources/etc"]})
        except BaseException as exc:  # noqa: BLE001 - 收集冲突异常用于断言
            return exc

    results = await asyncio.gather(submit_once(), submit_once())

    wins = [r for r in results if isinstance(r, Job)]
    conflicts = [r for r in results if isinstance(r, JobConflictError)]

    assert len(wins) == 1, f"应恰好 1 个成功，实际 {len(wins)}"
    assert len(conflicts) == 1, f"应恰好 1 个冲突，实际 {len(conflicts)}"

    # 冲突方带出冲突方的 job id，便于前端定位
    assert conflicts[0].data.get("running_job_id") == wins[0].id

    # 数据库层同样只应有一条 active 记录（防"两个都过了检查"这类更严重的缺陷）
    assert len(await repo.list_active()) == 1

    await await_job(runner, wins[0].id)
    await db.close()


async def test_concurrent_submit_does_not_duplicate_active_rows(tmp_path):
    """并发 submit 后，jobs 表中同仓库最多一条 running/pending（单写串行不变量）。

    覆盖 C1 的另一面：即便互斥判定将来被改动，DB 层的单写连接 + 状态
    判定也不应让两条 active 记录同时存在。
    """
    db, runner, repo = await make_runner(tmp_path, lines=BACKUP_LINES)

    results = await asyncio.gather(
        *(runner.submit("backup", params={"sources": ["/sources/etc"]}) for _ in range(4)),
        return_exceptions=True,
    )
    successes = [r for r in results if isinstance(r, Job)]
    assert len(successes) == 1, f"4 路并发应恰好 1 成功，实际 {len(successes)}"
    assert all(isinstance(r, JobConflictError) for r in results if not isinstance(r, Job))

    active = await repo.list_active()
    assert len(active) == 1
    assert active[0].id == successes[0].id

    await await_job(runner, successes[0].id)
    await db.close()


# --------------------------------- 任务维度提交（选项② R-026：task_id + task_loader）

async def test_task_submit_lands_task_id_and_uses_task_profile(tmp_path):
    """task_id 提交：jobs 落 task_id、policy_id=NULL；profile 经 task_loader 加载。"""
    db, runner, repo, task_repo = await make_runner_with_tasks(tmp_path, lines=BACKUP_LINES)
    task = await task_repo.create({"name": "每日全量", "sources": ["/sources/srv"], "excludes": []})

    job = await runner.submit("backup", task_id=task.id, trigger="manual")
    assert job.task_id == task.id
    assert job.policy_id is None  # 新任务 job 不落 legacy policy_id（§1.2 决策二）

    await await_job(runner, job.id)
    final = await repo.get(job.id)
    assert final.status == "success"

    # profile 来自任务而非 params：sources/tag 均取任务值
    args = runner._restic.last_args  # noqa: SLF001 - 测试替身断言（与既有 _tasks/_processes 同风格）
    assert args.targets == ("/sources/srv",)
    assert args.tags == (f"task-{task.id}",)

    rows, total = await AuditRepo(db).query(action="job.submit")
    assert total == 1
    assert '"task_id": ' + str(task.id) in (rows[0].detail or "")
    await db.close()


async def test_task_submit_profile_fallback_does_not_block(tmp_path):
    """task_loader 未注入时 profile 为空字典，由 params 兜底（不中断执行）。

    注意：task_id 必须用**真实存在的任务行**——``jobs.task_id`` 带指向
    ``backup_tasks(id)`` 的外键（0002 迁移；Q7 裁决：删任务时 ON DELETE SET NULL
    自动解绑），故「任务已删」在库里的真实表现为 task_id=NULL，而非指向孤儿 id。
    本用例校验的是「profile 解析为空时不阻塞」，与「任务不存在」是两件事。
    """
    db, runner, _ = await make_runner(tmp_path, lines=BACKUP_LINES)  # 无 task_loader
    task = await TaskRepo(db).create({"name": "无回调任务", "sources": ["/sources/srv"]})

    job = await runner.submit(
        "backup", task_id=task.id, trigger="manual", params={"sources": ["/sources/etc"]}
    )
    assert job.task_id == task.id

    await await_job(runner, job.id)
    final = await JobRepo(db).get(job.id)
    assert final.status == "success"  # params.sources 兜底生效
    await db.close()


async def test_manual_conflict_data_includes_running_task_name(tmp_path):
    """E12 409 data 增 running_task_name（US-026-04 AC2：UI 提示任务名）。"""
    db, runner, repo, task_repo = await make_runner_with_tasks(tmp_path)
    task = await task_repo.create({"name": "在跑任务", "sources": ["/sources/srv"]})
    await insert_job(repo, "existing", "running", task_id=task.id)

    with pytest.raises(JobConflictError) as excinfo:
        await runner.submit("backup", task_id=task.id, params={"sources": ["/sources/etc"]})
    data = excinfo.value.data
    assert data["running_job_id"] == "existing"
    assert data["running_task_name"] == "在跑任务"
    await db.close()


async def test_scheduled_conflict_skipped_job_carries_task_id(tmp_path):
    """scheduled 冲突：cancelled 跳过任务带 task_id（调度不排队，下周期再试）。"""
    db, runner, repo, task_repo = await make_runner_with_tasks(tmp_path)
    task = await task_repo.create({"name": "调度任务", "sources": ["/sources/srv"]})
    await insert_job(repo, "active", "running")

    skipped = await runner.submit("backup", task_id=task.id, trigger="scheduled")
    assert skipped.status == "cancelled"
    assert skipped.task_id == task.id
    assert skipped.trigger == "scheduled"

    rows, total = await AuditRepo(db).query(action="job.skipped_conflict")
    assert total == 1
    assert f'"task_id": {task.id}' in (rows[0].detail or "")
    await db.close()


async def test_concurrent_task_submit_exactly_one_wins(tmp_path):
    """真并发（asyncio.gather）：两个 task 维度 submit 同时竞争 → 恰好 1 成功。

    与「预置 running 再触发」的本质区别：两个协程在同一事件循环轮次内同时进入
    submit，真正并发竞争 ``_repo_lock``——竞态人工 review 发现不了（AGENTS.md 约定）。
    冲突方 data 带出运行中任务名，DB 层同样只应有一条 active 记录。
    """
    db, runner, repo, task_repo = await make_runner_with_tasks(tmp_path, lines=BACKUP_LINES)
    task = await task_repo.create({"name": "竞速任务", "sources": ["/sources/srv"]})

    async def submit_once() -> Job | BaseException:
        try:
            return await runner.submit(
                "backup", task_id=task.id, params={"sources": ["/sources/etc"]}
            )
        except BaseException as exc:  # noqa: BLE001 - 收集冲突异常用于断言
            return exc

    results = await asyncio.gather(submit_once(), submit_once())

    wins = [r for r in results if isinstance(r, Job)]
    conflicts = [r for r in results if isinstance(r, JobConflictError)]

    assert len(wins) == 1, f"应恰好 1 个成功，实际 {len(wins)}"
    assert len(conflicts) == 1, f"应恰好 1 个冲突，实际 {len(conflicts)}"
    assert conflicts[0].data.get("running_job_id") == wins[0].id
    assert conflicts[0].data.get("running_task_name") == "竞速任务"
    assert wins[0].task_id == task.id

    assert len(await repo.list_active()) == 1

    await await_job(runner, wins[0].id)
    await db.close()


# --------------------------------- X6：backup success 终态清 dry_run_pending（owner 裁决）

async def test_backup_success_invokes_callback_and_clears_pending(tmp_path):
    """X6：backup success 且 task_id 非空 → 回调以 task_id 调用、dry_run_pending 落库 0。

    覆盖「首次成功备份」这条清除路径（与 E36 预览清除互补，R-026-08 全语义）——
    绕过 UI 直接成功备份同样解除 Start 拦截。
    """
    db, runner, repo, task_repo = await make_runner_with_tasks(
        tmp_path, lines=BACKUP_LINES
    )
    # 回调记录调用并清 dry_run_pending（与 main.py 注入 clear_dry_run_pending 同构）
    calls: list[int] = []

    async def callback(task_id: int) -> None:
        calls.append(task_id)
        await task_repo.clear_dry_run_pending(task_id)

    runner._on_task_backup_success = callback  # noqa: SLF001 - 测试注入（构造期参数同款能力）

    task = await task_repo.create({"name": "首跑任务", "sources": ["/sources/srv"]})
    assert task.dry_run_pending is True  # 创建即置 1

    job = await runner.submit("backup", task_id=task.id)
    await await_job(runner, job.id)

    final = await repo.get(job.id)
    assert final.status == "success"
    assert calls == [task.id]
    assert (await task_repo.get(task.id)).dry_run_pending is False
    await db.close()



async def test_backup_success_callback_runs_outside_repo_lock(tmp_path):
    """N3 自死锁回归（真机取证 2026-10-25）：success 回调必须在**锁外**执行。

    现场复现：备份成功后 ``on_task_backup_success`` → ``SchedulerService.maybe_auto_forget``
    → ``JobRunner.submit("forget")``；旧实现把该回调放在 ``_run`` 的
    ``async with _repo_lock`` **内部** → ``asyncio.Lock`` 不可重入 → 本任务永久等待
    自己持有的锁 → 锁永不释放 → 此后每次「开始」都挂死在 ``submit`` 上（UI 表现：
    点了没反应、无新 job 行、无 console 报错，只有重启后端才恢复）。

    断言两条：①回调执行期间仓库锁已释放（根因的直接不变量）；
    ②回调内再次 submit + 执行派生任务能在超时内完成（不挂死）。
    """
    db, runner, repo, task_repo = await make_runner_with_tasks(tmp_path, lines=BACKUP_LINES)
    task = await task_repo.create({"name": "自动清理任务", "sources": ["/sources/srv"]})

    lock_held: list[bool] = []
    followup_ids: list[str] = []

    async def callback(task_id: int) -> None:
        # 与 scheduler.maybe_auto_forget 同构：读仓库 → submit 一个后续任务
        repo_url = await runner._repo_url("default")  # noqa: SLF001 - 测试取证内部状态
        lock_held.append(runner._repo_lock(repo_url).locked())  # noqa: SLF001
        followup = await runner.submit("unlock", task_id=task_id, trigger="scheduled")
        followup_ids.append(followup.id)

    runner._on_task_backup_success = callback  # noqa: SLF001 - 测试注入（构造期参数同款能力）

    job = await runner.submit("backup", task_id=task.id)
    # 5s 超时：旧实现在此永久挂起（TimeoutError），新实现正常返回
    await asyncio.wait_for(await_job(runner, job.id), timeout=5)

    assert lock_held == [False], "success 回调运行在仓库锁内 → 回调内 submit 会自死锁"
    assert len(followup_ids) == 1
    # 回调里 submit 的派生任务同样要在超时内跑完（锁已被正确让出）
    derived = runner._tasks.get(followup_ids[0])  # noqa: SLF001
    if derived is not None:
        await asyncio.wait_for(derived, timeout=5)
    followup = await repo.get(followup_ids[0])
    assert followup is not None and followup.status == "success"
    await db.close()


async def test_backup_failure_does_not_invoke_callback(tmp_path):
    """X6：failed 终态 → 不调用回调，dry_run_pending 保持 1（Start 拦截不解除）。"""
    db, runner, repo, task_repo = await make_runner_with_tasks(
        tmp_path, lines=BACKUP_LINES, exit_code=3
    )
    calls: list[int] = []

    async def callback(task_id: int) -> None:
        calls.append(task_id)

    runner._on_task_backup_success = callback  # noqa: SLF001

    task = await task_repo.create({"name": "失败任务", "sources": ["/sources/srv"]})
    job = await runner.submit("backup", task_id=task.id)
    await await_job(runner, job.id)

    final = await repo.get(job.id)
    assert final.status == "failed"
    assert calls == []
    assert (await task_repo.get(task.id)).dry_run_pending is True
    await db.close()


async def test_cancelled_job_does_not_invoke_callback(tmp_path):
    """X6：cancelled 终态 → 不调用回调（回调仅挂在 success 分支）。"""
    db, runner, repo, task_repo = await make_runner_with_tasks(
        tmp_path
    )
    calls: list[int] = []

    async def callback(task_id: int) -> None:
        calls.append(task_id)

    runner._on_task_backup_success = callback  # noqa: SLF001

    task = await task_repo.create({"name": "取消任务", "sources": ["/sources/srv"]})
    await insert_job(repo, "job-cancel-x6", "running", task_id=task.id)

    cancelled = await runner.cancel("job-cancel-x6")
    assert cancelled.status == "cancelled"
    assert calls == []
    assert (await task_repo.get(task.id)).dry_run_pending is True
    await db.close()


async def test_callback_failure_does_not_break_success_terminal(tmp_path):
    """X6：回调自身抛异常 → 吞掉并 WARNING，job 终态保持 success（不被改写为 failed）。"""
    db, runner, repo, task_repo = await make_runner_with_tasks(
        tmp_path, lines=BACKUP_LINES
    )

    async def broken_callback(task_id: int) -> None:
        raise RuntimeError("callback exploded")

    runner._on_task_backup_success = broken_callback  # noqa: SLF001

    task = await task_repo.create({"name": "回调炸任务", "sources": ["/sources/srv"]})
    job = await runner.submit("backup", task_id=task.id)
    await await_job(runner, job.id)

    final = await repo.get(job.id)
    assert final.status == "success"  # 终态未被回调异常篡改
    assert final.exit_code == 0
    await db.close()



# ---------------------------------------------------------------- R-016 forget 执行链（G3 + prune）

async def test_forget_without_keep_rule_fails_job(tmp_path):
    """G3 服务端兜底：无任何保留规则的 forget → 执行期 ValidationError → job failed。

    E21 预览与 E12 执行两侧同拒（routers/ops.py require_keep_rule 与
    JobRunner._build_args forget 分支同口径）——绕过预览直接提交「全删」语义
    不可能成功。
    """
    db, runner, jobs = await make_runner(tmp_path)
    job = await runner.submit("forget", trigger="manual", params={"tags": ["task-1"]})
    await await_job(runner, job.id)

    final = await jobs.get(job.id)
    assert final is not None and final.status == "failed"
    # _run 兜底 summary 只记异常类型名（不泄漏内部细节，既有口径）；G3 拒绝的
    # 可读原因在任务日志与 E21 预览侧（require_keep_rule 的 422 文案）。
    assert "ValidationError" in (final.summary or "")


async def test_forget_with_keep_rule_and_prune_passes_flags(tmp_path):
    """有保留规则的 forget 正常执行；prune=True → WhitelistArgs.prune 透传。"""
    db, runner, jobs = await make_runner(tmp_path)
    fake = runner._restic  # FakeRestic
    job = await runner.submit(
        "forget",
        trigger="manual",
        params={"tags": ["task-1"], "keep_daily": 7, "prune": True},
    )
    await await_job(runner, job.id)

    final = await jobs.get(job.id)
    assert final is not None and final.status == "success"
    args = fake.last_args
    assert args is not None
    assert args.keep_daily == 7
    assert args.prune is True
    assert args.dry_run is False  # 执行路径绝不带 --dry-run
    assert args.tags == ("task-1",)
    await db.close()

async def test_finalize_invalidates_status_cache_before_terminal():
    """E30 时序护栏（初始化后列表状态不同步的回归锁）：

    ``_finalize`` 必须先触发 ``on_finish``（清 E30 进程内状态缓存）再写终态入库
    （``jobs.finalize``）。若顺序颠倒，SSE 流轮询到 DB 终态后会先于清缓存向客户端
    发 ``done`` 事件，前端据此重取会命中终态前写入的脏缓存 = 表现为「初始化完了
    按钮还在 / 不可达仍在」，需刷新页面才消失。把清缓存提前到终态入库之前，``done``
    发出时缓存已空，前端重取必得新鲜值。
    """
    order: list[str] = []
    runner = JobRunner.__new__(JobRunner)  # 绕过 __init__ 的 DB/settings 依赖，仅测调用时序
    runner._on_finish = lambda: order.append("on_finish")

    async def fake_finalize(job_id, status, **kwargs):  # noqa: ANN001, ANN002, ANN003
        order.append("finalize")
        return None

    runner._jobs = MagicMock()
    runner._jobs.finalize = fake_finalize

    await runner._finalize("job-x", "success")

    assert order == ["on_finish", "finalize"]
