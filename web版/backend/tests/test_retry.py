"""批次 D C3 任务级失败重试（Duplicati number-of-retries 等价物）回归。

断言分三层：
- **预算**：manual 触发恒不重试；scheduled 触发按任务级 ``retry_count``；非法值 → 0
  （fail-safe 方向是不重试）；上限钳制（无上限的重试会长期占着仓库互斥锁）；
- **退避**：指数增长且钳上限（30s→60s→…→900s 封顶）；
- **执行链路**：失败→重试→成功（attempts 记账、摘要留痕、日志可见）、
  重试耗尽仍失败（终态 failed、退出码取最后一次）、手动触发零重试。

退避时间用 monkeypatch 压到 0（真实 30s 起跳会让测试变成分钟级）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.constants import RETRY_BASE_DELAY_SECONDS, RETRY_COUNT_MAX, RETRY_MAX_DELAY_SECONDS
from app.db.database import Database
from app.db.repositories.audit_repo import AuditRepo
from app.db.repositories.job_repo import JobRepo
from app.db.repositories.task_repo import TaskRepo
from app.errors import ValidationError
from app.services import job_runner as job_runner_module
from app.services.job_runner import JobRunner
from app.services.parser import OutputParser
from app.services.task_service import TaskService
from app.settings import Settings

_BASE_PAYLOAD = {
    "name": "重试任务",
    "sources": ["/etc"],
    "keep_daily": 7,
    "check_sample_ratio": 0.02,
    "monthly_full_day": 1,
}


class _FakeProcess:
    def __init__(self, exit_code: int) -> None:
        self._exit_code = exit_code

    async def lines(self):  # noqa: ANN201
        return
        yield  # pragma: no cover

    async def wait(self) -> int:
        return self._exit_code

    def terminate(self) -> None:  # pragma: no cover
        pass

    def kill(self) -> None:  # pragma: no cover
        pass


class _FakeRestic:
    """ResticCmd 替身：按队列给出退出码，记录 spawn 次数。"""

    def __init__(self, exit_codes: list[int]) -> None:
        self._exit_codes = list(exit_codes)
        self.spawn_count = 0

    def build(self, verb, *, args, env_extras=None):  # noqa: ANN001, ANN202
        return ["restic-fake", verb]

    def build_env(self, *, repo_url, repo_id="default", env_extras=None):  # noqa: ANN001, ANN202
        return {}

    async def spawn(self, argv, log_path, *, env=None):  # noqa: ANN001, ANN202
        self.spawn_count += 1
        code = self._exit_codes.pop(0) if self._exit_codes else 0
        return _FakeProcess(code)


async def _make(tmp_path, *, retry_count: int = 0, exit_codes: list[int], monkeypatch):
    monkeypatch.setattr(job_runner_module, "_retry_delay", lambda attempt: 0.0)
    db = Database(tmp_path / "retry.db")
    await db.connect()
    settings = Settings(data_dir=str(tmp_path))
    settings.jobs_dir.mkdir(parents=True, exist_ok=True)
    task_repo = TaskRepo(db)
    service = TaskService(task_repo, settings)
    task = await task_repo.create(
        service.normalize({**_BASE_PAYLOAD, "name": "重试任务", "retry_count": retry_count})
    )
    fake = _FakeRestic(exit_codes)
    runner = JobRunner(
        db,
        fake,  # type: ignore[arg-type]
        OutputParser(),
        AuditRepo(db),
        settings=settings,
        task_loader=task_repo.get_dict,
    )
    return db, runner, fake, JobRepo(db), task


async def _await(runner: JobRunner, job_id: str) -> None:
    task = runner._tasks.get(job_id)
    if task is not None:
        await task


# ---------------------------------------------------------------- 预算与退避（纯函数）


def _runner_stub() -> JobRunner:
    return JobRunner.__new__(JobRunner)  # 只测纯静态方法，不需要构造


def test_retry_budget_manual_is_zero() -> None:
    class _Job:
        trigger = "manual"

    assert JobRunner._retry_budget(_Job(), {"retry_count": 2}) == 0


def test_retry_budget_scheduled_uses_task_value() -> None:
    class _Job:
        trigger = "scheduled"

    assert JobRunner._retry_budget(_Job(), {"retry_count": 2}) == 2
    assert JobRunner._retry_budget(_Job(), {"retry_count": 0}) == 0
    assert JobRunner._retry_budget(_Job(), {}) == 0  # 无任务 profile


@pytest.mark.parametrize("raw", ["abc", None, {"x": 1}])
def test_retry_budget_invalid_is_zero(raw: object) -> None:
    """非法值一律 0（fail-safe：不重试绝不比乱重试更糟）。"""

    class _Job:
        trigger = "scheduled"

    assert JobRunner._retry_budget(_Job(), {"retry_count": raw}) == 0


def test_retry_budget_clamped() -> None:
    class _Job:
        trigger = "scheduled"

    assert JobRunner._retry_budget(_Job(), {"retry_count": 99}) == RETRY_COUNT_MAX
    assert JobRunner._retry_budget(_Job(), {"retry_count": -3}) == 0


def test_retry_delay_is_exponential_and_capped() -> None:
    assert job_runner_module._retry_delay(1) == RETRY_BASE_DELAY_SECONDS
    assert job_runner_module._retry_delay(2) == RETRY_BASE_DELAY_SECONDS * 2
    assert job_runner_module._retry_delay(20) == RETRY_MAX_DELAY_SECONDS  # 钳上限


# ---------------------------------------------------------------- 执行链路


async def test_scheduled_failure_then_success(tmp_path, monkeypatch) -> None:
    """首次失败 + 重试成功：终态 success，attempts=2 进审计与摘要。"""
    db, runner, fake, jobs, task = await _make(
        tmp_path, retry_count=2, exit_codes=[1, 0], monkeypatch=monkeypatch
    )
    job = await runner.submit("backup", task_id=task.id, trigger="scheduled")  # type: ignore[arg-type]
    await _await(runner, job.id)

    final = await jobs.get(job.id)
    assert final is not None
    assert final.status == "success"
    assert final.exit_code == 0
    assert "重试 1 次后成功" in (final.summary or "")
    assert fake.spawn_count == 2

    rows = await db.fetch_all(
        "SELECT detail FROM audit_logs WHERE action = 'job.finished' AND target = ?", (job.id,)
    )
    assert rows and '"attempts": 2' in str(rows[0]["detail"])
    await db.close()


async def test_retry_exhausted_keeps_failed(tmp_path, monkeypatch) -> None:
    """重试全部失败：终态 failed，spawn 次数 = 首次 + 重试次数。"""
    db, runner, fake, jobs, task = await _make(
        tmp_path, retry_count=2, exit_codes=[1, 1, 1], monkeypatch=monkeypatch
    )
    job = await runner.submit("backup", task_id=task.id, trigger="scheduled")  # type: ignore[arg-type]
    await _await(runner, job.id)

    final = await jobs.get(job.id)
    assert final is not None
    assert final.status == "failed"
    assert fake.spawn_count == 3
    await db.close()


async def test_manual_never_retries(tmp_path, monkeypatch) -> None:
    """手动触发恒不重试（用户点「立即运行」是立刻要看结果）。"""
    db, runner, fake, jobs, task = await _make(
        tmp_path, retry_count=2, exit_codes=[1], monkeypatch=monkeypatch
    )
    job = await runner.submit("backup", task_id=task.id, trigger="manual")  # type: ignore[arg-type]
    await _await(runner, job.id)

    final = await jobs.get(job.id)
    assert final is not None
    assert final.status == "failed"
    assert fake.spawn_count == 1
    await db.close()


async def test_retry_disabled_by_task(tmp_path, monkeypatch) -> None:
    """retry_count=0 = 显式关闭重试。"""
    db, runner, fake, jobs, task = await _make(
        tmp_path, retry_count=0, exit_codes=[1], monkeypatch=monkeypatch
    )
    job = await runner.submit("backup", task_id=task.id, trigger="scheduled")  # type: ignore[arg-type]
    await _await(runner, job.id)

    final = await jobs.get(job.id)
    assert final is not None
    assert final.status == "failed"
    assert fake.spawn_count == 1
    await db.close()


async def test_retry_logged_in_job_log(tmp_path, monkeypatch) -> None:
    """重试痕迹进任务日志（SSE/E27 可见，禁止静默重试）。"""
    db, runner, _fake, jobs, task = await _make(
        tmp_path, retry_count=1, exit_codes=[1, 0], monkeypatch=monkeypatch
    )
    job = await runner.submit("backup", task_id=task.id, trigger="scheduled")  # type: ignore[arg-type]
    await _await(runner, job.id)

    final = await jobs.get(job.id)
    assert final is not None
    log_text = Path(final.log_path).read_text(encoding="utf-8") if Path(final.log_path).exists() else ""
    assert "[retry]" in log_text
    await db.close()


# ---------------------------------------------------------------- 存储与写侧校验


async def test_retry_count_roundtrip(tmp_path) -> None:
    db = Database(tmp_path / "retry.db")
    await db.connect()
    settings = Settings(data_dir=str(tmp_path))
    repo = TaskRepo(db)
    service = TaskService(repo, settings)

    default_task = await repo.create(service.normalize(dict(_BASE_PAYLOAD)))
    assert default_task.retry_count == 2  # 0006 DDL 缺省与 API 缺省一致

    custom = await repo.create(service.normalize({**_BASE_PAYLOAD, "retry_count": 0}))
    assert custom.retry_count == 0

    clamped = await repo.create(service.normalize({**_BASE_PAYLOAD, "retry_count": 99}))
    assert clamped.retry_count == RETRY_COUNT_MAX

    profile = await repo.get_dict(clamped.id)
    assert profile is not None
    assert profile["retry_count"] == RETRY_COUNT_MAX
    await db.close()


async def test_retry_count_rejects_negative_and_non_int(tmp_path) -> None:
    db = Database(tmp_path / "retry.db")
    await db.connect()
    service = TaskService(TaskRepo(db), Settings(data_dir=str(tmp_path)))
    for bad in (-1, "abc", 1.5):
        with pytest.raises(ValidationError):
            service.normalize({**_BASE_PAYLOAD, "retry_count": bad})
    await db.close()
