"""JobRunner 六状态机测试（§10.1 状态机层）：表驱动转换、冲突拒绝、启动恢复、取消。

断言基线（主理人裁决）：recover_on_startup 把遗留 running **与 pending** 一并置 interrupted。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from app.constants import (
    ACTIVE_JOB_STATUSES,
    JOB_STATUSES,
    TERMINAL_JOB_STATUSES,
)
from app.db.database import Database
from app.db.repositories.audit_repo import AuditRepo
from app.db.repositories.job_repo import Job, JobRepo
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
    '{"message_type":"error","during":"archival","item":"/host/etc/shadow","error":"permission denied"}',
    '{"message_type":"summary","total_files_processed":2,"total_bytes_processed":100,'
    '"snapshot_id":"a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2",'
    '"snapshot_time":"2026-10-10T01:38:00Z"}',
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

    def build(self, verb, *, args, env_extras=None):  # noqa: ANN001, ANN202
        return ["restic-fake", verb]

    def build_env(self, *, repo_url, env_extras=None):  # noqa: ANN001, ANN202
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


async def await_job(runner: JobRunner, job_id: str) -> None:
    """等待 submit 派生的后台执行任务结束（保证确定性）。"""
    task = runner._tasks.get(job_id)
    if task is not None:
        await task


async def insert_job(repo: JobRepo, job_id: str, status: str, *, trigger: str = "manual") -> None:
    await repo.create(
        job_id=job_id,
        job_type="backup",
        trigger=trigger,
        log_path=f"/tmp/{job_id}.log",
        status=status,
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

    job = await runner.submit("backup", params={"sources": ["/host/etc"]})
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


async def test_nonzero_exit_maps_to_failed(tmp_path):
    db, runner, repo = await make_runner(tmp_path, lines=BACKUP_LINES, exit_code=3)

    job = await runner.submit("backup", params={"sources": ["/host/etc"]})
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

    job = await runner.submit("backup", params={"sources": ["/host/etc"]})
    await await_job(runner, job.id)

    final = await repo.get(job.id)
    assert final.status == "failed"
    assert final.exit_code == -1
    assert "FileNotFoundError" in (final.summary or "")
    await db.close()


async def test_scheduled_trigger_runs_normally_when_free(tmp_path):
    db, runner, repo = await make_runner(tmp_path, lines=BACKUP_LINES)
    job = await runner.submit("backup", trigger="scheduled", params={"sources": ["/host/etc"]})
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
        await runner.submit("backup", params={"sources": ["/host/etc"]})
    assert excinfo.value.data == {"running_job_id": "existing"}
    await db.close()


async def test_manual_conflict_also_triggers_on_pending(tmp_path):
    db, runner, repo = await make_runner(tmp_path)
    await insert_job(repo, "queued", "pending")
    with pytest.raises(JobConflictError):
        await runner.submit("check")
    await db.close()


async def test_scheduled_conflict_records_cancelled_job_and_audit(tmp_path):
    """scheduled 冲突不 409：落一条 cancelled 任务 + 审计后跳过（§5.1 E12 注）。"""
    db, runner, repo = await make_runner(tmp_path)
    audit = AuditRepo(db)
    runner._audit = audit
    await insert_job(repo, "active", "running")

    skipped = await runner.submit("backup", trigger="scheduled", params={"sources": ["/host/etc"]})
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
    job = await runner.submit("backup", params={"sources": ["/host/etc"]})
    await await_job(runner, job.id)
    with pytest.raises(NotCancellableError):
        await runner.cancel(job.id)
    assert runner._tasks == {}
    await db.close()


# ---------------------------------------------------------------- 并发互斥（G6 真并发）

# ✅ **本节用例在本机 Windows 上可正常通过**（2026-10-11 复核：连跑 3 轮全量
# 均为 117 passed）。
# 此前记录的「凡经 aiosqlite 的用例都会挂起」是**误判**，已撤销：当时的挂起由
# 本会话内几条超时被杀/未回收的 python 进程引起（连续跑 pytest 与多次超时中断
# 叠加导致），清理进程后即恢复，与事件循环策略、aiosqlite 版本均无关。
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
            return await runner.submit("backup", params={"sources": ["/host/etc"]})
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
        *(runner.submit("backup", params={"sources": ["/host/etc"]}) for _ in range(4)),
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
