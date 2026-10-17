"""批次 D C1 任务级前后钩子（Duplicati RunScript 等价物）回归。

分三层断言：
- 命令切分与执行（:mod:`app.services.hooks`）——不经 shell 的 argv 形态、超时、
  输出入日志、失败原因；
- JobRunner 接线：pre 失败阻断后续阶段、post 失败翻转终态、审计留痕；
- 存储层：TaskRepo/TaskService 的钩子列往返与写侧校验（422 尽早失败）。

跨平台：钩子命令一律用 ``sys.executable -c ...`` 构造（Windows 无 /bin/true、
无 sh，用 shell 内建命令会让测试在 CI 的 Linux 镜像里通过、本机失败）。
"""

from __future__ import annotations

import shlex
import sys
from pathlib import Path

import pytest

from app.constants import AUDIT_HOOK_RUN
from app.db.database import Database
from app.db.repositories.audit_repo import AuditRepo
from app.db.repositories.job_repo import JobRepo
from app.db.repositories.task_repo import TaskRepo
from app.errors import ValidationError
from app.services.hooks import HOOK_PHASE_POST, HOOK_PHASE_PRE, run_hook, split_command
from app.services.job_runner import JobRunner
from app.services.parser import OutputParser
from app.services.task_service import TaskService
from app.settings import Settings

# normalize 是全量语义（E35 upsert 全字段），缺 check_sample_ratio 会被拒
_BASE_PAYLOAD = {
    "name": "钩子任务",
    "sources": ["/etc"],
    "keep_daily": 7,
    "check_sample_ratio": 0.02,
    "monthly_full_day": 1,
}


# ---------------------------------------------------------------- 命令切分（纯函数）


def test_split_command_returns_argv() -> None:
    assert split_command('/opt/hook/pre.sh --tag daily') == ('/opt/hook/pre.sh', '--tag', 'daily')


@pytest.mark.parametrize("raw", ["", "   ", None])
def test_split_command_empty_is_none(raw: str | None) -> None:
    assert split_command(raw or "") is None


def test_split_command_keeps_quoted_argument() -> None:
    argv = split_command('/bin/echo "a b" c')
    assert argv == ('/bin/echo', 'a b', 'c')


def test_split_command_rejects_unbalanced_quote() -> None:
    with pytest.raises(ValueError, match="解析失败"):
        split_command('/bin/echo "a b')


def test_split_command_rejects_newline() -> None:
    with pytest.raises(ValueError):
        split_command('/bin/echo a\nrm -rf /')


def test_split_command_rejects_too_long() -> None:
    with pytest.raises(ValueError, match="上限"):
        split_command("x" * 2000)


# ---------------------------------------------------------------- 执行（真子进程，不经 shell）


def _py(code: str) -> str:
    """构造一条跨平台命令串（``<python> -c <code>``）。"""
    return shlex.join([sys.executable, "-c", code])


async def test_run_hook_success_writes_log(tmp_path) -> None:
    log_path = tmp_path / "job.log"
    outcome = await run_hook(
        command=_py("print('hook-ok')"), phase=HOOK_PHASE_PRE, log_path=log_path
    )
    assert outcome.ok
    assert outcome.exit_code == 0
    assert "hook-ok" in outcome.output
    text = log_path.read_text(encoding="utf-8")
    assert text.startswith("[hook][pre] $ ")
    assert sys.executable in text  # 命令串经 shlex.join 回显（含引号，故只断言包含）
    assert "[hook][pre] exit=0" in text


async def test_run_hook_failure_reports_exit_code(tmp_path) -> None:
    outcome = await run_hook(
        command=_py("import sys; sys.exit(3)"), phase=HOOK_PHASE_POST, log_path=tmp_path / "job.log"
    )
    assert not outcome.ok
    assert outcome.exit_code == 3
    assert outcome.reason() == "退出码 3"


async def test_run_hook_timeout_is_killed(tmp_path) -> None:
    outcome = await run_hook(
        command=_py("import time; time.sleep(30)"),
        phase=HOOK_PHASE_PRE,
        log_path=tmp_path / "job.log",
        timeout_seconds=0.5,
    )
    assert not outcome.ok
    assert outcome.timed_out
    assert "超时" in outcome.reason()


async def test_run_hook_missing_binary_is_failure(tmp_path) -> None:
    outcome = await run_hook(
        command=shlex.join(["definitely-not-a-real-binary-xyz", "--flag"]),
        phase=HOOK_PHASE_PRE,
        log_path=tmp_path / "job.log",
    )
    assert not outcome.ok
    assert "无法执行" in outcome.output


async def test_run_hook_empty_command_is_noop(tmp_path) -> None:
    log_path = tmp_path / "job.log"
    outcome = await run_hook(command="   ", phase=HOOK_PHASE_PRE, log_path=log_path)
    assert outcome.ok
    assert outcome.output == ""
    assert not log_path.exists()  # 未配置 = 零副作用（连日志都不写）


async def test_run_hook_illegal_command_is_failure(tmp_path) -> None:
    log_path = tmp_path / "job.log"
    outcome = await run_hook(command='/bin/echo "oops', phase=HOOK_PHASE_PRE, log_path=log_path)
    assert not outcome.ok
    assert not outcome.parsed
    assert "命令非法" in log_path.read_text(encoding="utf-8")


async def test_run_hook_truncates_huge_output(tmp_path) -> None:
    outcome = await run_hook(
        command=_py("print('x' * 20000)"), phase=HOOK_PHASE_POST, log_path=tmp_path / "job.log"
    )
    assert outcome.ok
    assert "输出已截断" in outcome.output


# ---------------------------------------------------------------- JobRunner 接线

class _FakeProcess:
    def __init__(self, log_path: Path, exit_code: int = 0) -> None:
        self._log_path = log_path
        self._exit_code = exit_code

    async def lines(self):  # noqa: ANN202
        return
        yield  # pragma: no cover

    async def wait(self) -> int:
        return self._exit_code

    def terminate(self) -> None:  # pragma: no cover
        pass

    def kill(self) -> None:  # pragma: no cover
        pass


class _FakeRestic:
    """ResticCmd 替身：记录最近一次 build 的 args（pre 钩子失败时应为 None）。"""

    def __init__(self, exit_code: int = 0) -> None:
        self.last_args = None
        self._exit_code = exit_code

    def build(self, verb, *, args, env_extras=None):  # noqa: ANN001, ANN202
        self.last_args = args
        return ["restic-fake", verb]

    def build_env(self, *, repo_url, repo_id="default", env_extras=None):  # noqa: ANN001, ANN202
        return {}

    async def spawn(self, argv, log_path, *, env=None):  # noqa: ANN001, ANN202
        return _FakeProcess(Path(log_path), self._exit_code)


async def _make_runner(tmp_path, *, pre: str = "", post: str = "", exit_code: int = 0):
    """建库 + 建**真实任务行**（jobs.task_id 外键生效，替身 loader 会撞 FK）+ runner。

    钩子走真实 TaskRepo.get_dict（顺带覆盖存储层 → profile 的取值链路）。
    """
    db = Database(tmp_path / "jobs.db")
    await db.connect()
    settings = Settings(data_dir=str(tmp_path))
    settings.jobs_dir.mkdir(parents=True, exist_ok=True)
    task_repo = TaskRepo(db)
    service = TaskService(task_repo, settings)
    task = await task_repo.create(
        service.normalize({**_BASE_PAYLOAD, "name": "钩子任务", "pre_cmd": pre, "post_cmd": post})
    )
    fake = _FakeRestic(exit_code=exit_code)
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


def _log_text(path: Path) -> str:
    """读取任务日志；钩子与 fake restic 都不写日志时文件可能不存在。"""
    return path.read_text(encoding="utf-8") if path.exists() else ""


async def test_pre_hook_failure_blocks_restic_phase(tmp_path) -> None:
    """C1 核心：pre 失败 → 任务 failed，且 **restic 阶段根本没有被拼装**（真阻断）。"""
    db, runner, fake, jobs, task = await _make_runner(
        tmp_path, pre=_py("import sys; sys.exit(2)")
    )
    job = await runner.submit("backup", task_id=task.id, trigger="scheduled")
    await _await(runner, job.id)

    final = await jobs.get(job.id)
    assert final is not None
    assert final.status == "failed"
    assert "前置钩子失败" in (final.summary or "")
    assert fake.last_args is None  # 阻断：restic 阶段未进入

    rows = await db.fetch_all(
        "SELECT action, detail, result FROM audit_logs WHERE action = ?", (AUDIT_HOOK_RUN,)
    )
    assert len(rows) == 1
    assert "pre" in str(rows[0]["detail"])
    assert str(rows[0]["result"]) == "failed"
    await db.close()


async def test_post_hook_failure_flips_status_to_failed(tmp_path) -> None:
    """post 钩子只在成功后跑；它失败 → 终态从 success 翻转为 failed。"""
    db, runner, fake, jobs, task = await _make_runner(
        tmp_path, post=_py("import sys; sys.exit(4)")
    )
    job = await runner.submit("backup", task_id=task.id, trigger="scheduled")
    await _await(runner, job.id)

    final = await jobs.get(job.id)
    assert final is not None
    assert final.status == "failed"
    assert "后置钩子失败" in (final.summary or "")
    assert fake.last_args is not None  # restic 阶段照常执行过

    rows = await db.fetch_all(
        "SELECT detail, result FROM audit_logs WHERE action = ?", (AUDIT_HOOK_RUN,)
    )
    assert len(rows) == 1
    assert "post" in str(rows[0]["detail"])
    await db.close()


async def test_hooks_success_keeps_job_success(tmp_path) -> None:
    db, runner, _fake, jobs, task = await _make_runner(
        tmp_path, pre=_py("print('pre')"), post=_py("print('post')")
    )
    job = await runner.submit("backup", task_id=task.id, trigger="scheduled")
    await _await(runner, job.id)

    final = await jobs.get(job.id)
    assert final is not None
    assert final.status == "success"
    log_text = _log_text(Path(final.log_path))
    assert "[hook][pre] exit=0" in log_text
    assert "[hook][post] exit=0" in log_text
    await db.close()


async def test_post_hook_not_run_when_restic_failed(tmp_path) -> None:
    """restic 阶段失败 → post 钩子不跑（只对成功负责）。"""
    db, runner, _fake, jobs, task = await _make_runner(
        tmp_path, post=_py("print('post')"), exit_code=1
    )
    job = await runner.submit("backup", task_id=task.id, trigger="scheduled")
    await _await(runner, job.id)

    final = await jobs.get(job.id)
    assert final is not None
    assert final.status == "failed"
    log_text = _log_text(Path(final.log_path))
    assert "[hook][post]" not in log_text
    await db.close()


async def test_job_without_task_runs_no_hooks(tmp_path) -> None:
    """无任务关联（legacy 策略 job / 直接提交）→ 钩子零介入，行为逐字节不变。"""
    db, runner, _fake, jobs, _task = await _make_runner(
        tmp_path, pre=_py("print('pre')"), post=_py("print('post')")
    )
    job = await runner.submit("backup", params={"sources": ["/sources/etc"]})
    await _await(runner, job.id)

    final = await jobs.get(job.id)
    assert final is not None
    assert final.status == "success"
    assert "[hook]" not in _log_text(Path(final.log_path))
    await db.close()


# ---------------------------------------------------------------- 存储层（列往返 + 写侧校验）


async def test_task_hooks_roundtrip(tmp_path) -> None:
    db = Database(tmp_path / "jobs.db")
    await db.connect()
    settings = Settings(data_dir=str(tmp_path))
    repo = TaskRepo(db)
    service = TaskService(repo, settings)
    task = await repo.create(
        service.normalize({**_BASE_PAYLOAD, "name": "钩子任务",
                           "pre_cmd": "/opt/pre.sh --daily", "post_cmd": "/opt/post.sh"})
    )
    assert task.pre_cmd == "/opt/pre.sh --daily"
    assert task.post_cmd == "/opt/post.sh"

    profile = await repo.get_dict(task.id)
    assert profile is not None
    assert profile["pre_cmd"] == "/opt/pre.sh --daily"
    assert profile["post_cmd"] == "/opt/post.sh"

    # 缺省（不传钩子字段）→ 空串 = 未配置
    plain = await repo.create(service.normalize({**_BASE_PAYLOAD, "name": "无钩子任务"}))
    assert plain.pre_cmd == ""
    assert plain.post_cmd == ""
    await db.close()


async def test_task_hooks_reject_bad_command(tmp_path) -> None:
    db = Database(tmp_path / "jobs.db")
    await db.connect()
    settings = Settings(data_dir=str(tmp_path))
    service = TaskService(TaskRepo(db), settings)
    for bad in ('/bin/echo "oops', "x" * 2000, "cmd\nsecond"):
        with pytest.raises(ValidationError):
            service.normalize({**_BASE_PAYLOAD, "name": "坏钩子", "pre_cmd": bad})
    await db.close()


async def test_task_hooks_blank_is_unset(tmp_path) -> None:
    db = Database(tmp_path / "jobs.db")
    await db.connect()
    settings = Settings(data_dir=str(tmp_path))
    service = TaskService(TaskRepo(db), settings)
    values = service.normalize({**_BASE_PAYLOAD, "name": "空钩子", "pre_cmd": "   "})
    assert values["pre_cmd"] == ""
    await db.close()
