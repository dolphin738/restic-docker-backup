"""批次 D C4 多目的地 / copy / key 回归。

分层断言：
- ``_normalize_mirror_ids``：去空白、丢空、保序去重、超限（>3）硬拒 422；
- restic verb 拼装：copy 不把仓库 url 带进 argv、拒绝 --exclude；key 子命令走
  具名字段、--new-password-file 仅落路径（S3）；
- JobRunner 镜像阶段：备份成功后对镜像仓执行 copy（同任务日志/AUDIT_COPY_RUN），
  镜像失败不翻转主目的地终态、自指镜像被优雅跳过。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.constants import AUDIT_COPY_RUN, MIRROR_REPO_IDS_MAX
from app.db.database import Database
from app.db.repositories.audit_repo import AuditRepo
from app.db.repositories.job_repo import JobRepo
from app.db.repositories.repo_repo import ReposRepo
from app.db.repositories.task_repo import TaskRepo
from app.errors import ValidationError
from app.services.job_runner import JobRunner
from app.services.parser import OutputParser
from app.services.restic import ResticCmd, WhitelistArgs
from app.services.task_service import TaskService, _normalize_mirror_ids
from app.settings import Settings

_BASE_PAYLOAD = {
    "name": "镜像任务",
    "sources": ["/etc"],
    "keep_daily": 7,
    "check_sample_ratio": 0.02,
    "monthly_full_day": 1,
}


# ---------------------------------------------------------------- 归一（纯函数）


def test_normalize_mirror_ids_dedup_preserves_order() -> None:
    assert _normalize_mirror_ids(["mir", "src", "mir", "", "  mir  "]) == ["mir", "src"]


def test_normalize_mirror_ids_none_is_empty() -> None:
    assert _normalize_mirror_ids(None) == []


def test_normalize_mirror_ids_json_string_is_parsed() -> None:
    assert _normalize_mirror_ids('["a", "b"]') == ["a", "b"]


@pytest.mark.parametrize("bad", ["not-json", "5", '"a"', "key=value", 1.5, 9])
def test_normalize_mirror_ids_rejects_non_list(bad) -> None:  # noqa: ANN001
    with pytest.raises(ValidationError):
        _normalize_mirror_ids(bad)


def test_normalize_mirror_ids_rejects_over_max() -> None:
    with pytest.raises(ValidationError, match="上限"):
        _normalize_mirror_ids(["r1", "r2", "r3", "r4"])


def test_normalize_mirror_ids_exactly_max_is_ok() -> None:
    ids = [f"r{i}" for i in range(1, MIRROR_REPO_IDS_MAX + 1)]
    assert _normalize_mirror_ids(ids) == ids


# ---------------------------------------------------------------- restic verb 拼装

_RESTIC = ResticCmd()


def test_copy_argv_excludes_repo_url_and_rejects_excludes() -> None:
    argv = _RESTIC.build("copy", args=WhitelistArgs(json_output=False))
    assert "copy" in argv
    assert "local:/data/src" not in argv  # 源仓地址只走 env，绝不进 argv（S3）
    with pytest.raises(ValidationError):
        _RESTIC.build("copy", args=WhitelistArgs(json_output=False, excludes=["/x"]))


def test_key_passwd_argv_carries_new_password_file_only() -> None:
    argv = _RESTIC.build(
        "key",
        args=WhitelistArgs(json_output=False, key_target="passwd", new_password_file="/tmp/new.pw"),
    )
    assert "key" in argv
    assert "passwd" in argv
    assert "--new-password-file" in argv
    assert "/tmp/new.pw" in argv  # 新密码只落路径（S3）


def test_key_rejects_invalid_subcommand() -> None:
    with pytest.raises(ValidationError):
        _RESTIC.build("key", args=WhitelistArgs(json_output=False, key_target="bogus"))


def test_key_remove_requires_id() -> None:
    with pytest.raises(ValidationError):
        _RESTIC.build("key", args=WhitelistArgs(json_output=False, key_target="remove"))


# ---------------------------------------------------------------- JobRunner 镜像阶段


class _FakeProcess:
    def __init__(self, log_path: Path, exit_code: int = 0) -> None:
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
    """按 verb 区分配退出码：backup 与 copy 可独立控制（镜像失败不影响主目的地）。"""

    def __init__(self, backup_exit: int = 0, copy_exit: int = 0) -> None:
        self._backup_exit = backup_exit
        self._copy_exit = copy_exit

    def build(self, verb, *, args, env_extras=None):  # noqa: ANN001, ANN202
        return ["restic-fake", verb]

    def build_env(self, *, repo_url, repo_id="default", env_extras=None):  # noqa: ANN001, ANN202
        return {}

    async def spawn(self, argv, log_path, *, env=None):  # noqa: ANN001, ANN202
        is_copy = len(argv) > 1 and argv[1] == "copy"
        code = self._copy_exit if is_copy else self._backup_exit
        return _FakeProcess(Path(log_path), code)


async def _make_mirror_runner(tmp_path, *, mirror_ids, backup_exit=0, copy_exit=0):
    # 口令文件根目录须指向临时区（默认是容器路径 /data/repos，本机不存在）
    os.environ["PASSFILE_ROOT"] = str(tmp_path / "repos")
    db = Database(tmp_path / "jobs.db")
    await db.connect()
    settings = Settings(data_dir=str(tmp_path))
    settings.jobs_dir.mkdir(parents=True, exist_ok=True)
    task_repo = TaskRepo(db)
    service = TaskService(task_repo, settings)
    task = await task_repo.create(
        service.normalize({**_BASE_PAYLOAD, "mirror_repo_ids": mirror_ids})
    )
    repos_repo = ReposRepo(db)
    # default 仓由 0002 迁移种子提供（作为源仓）；镜像仓另行创建
    await repos_repo.create("mir", "镜像仓", url="local:/data/mir")
    # 镜像仓口令文件须存在（passfile_status == configured），否则被前置拒绝
    mirror_pw = settings.passfile_path("mir")
    mirror_pw.parent.mkdir(parents=True, exist_ok=True)
    mirror_pw.write_text("mirror-pw", encoding="utf-8")
    fake = _FakeRestic(backup_exit=backup_exit, copy_exit=copy_exit)
    runner = JobRunner(
        db,
        fake,  # type: ignore[arg-type]
        OutputParser(),
        AuditRepo(db),
        settings=settings,
        task_loader=task_repo.get_dict,
        repos_repo=repos_repo,
    )
    return db, runner, fake, JobRepo(db), task


async def _await(runner: JobRunner, job_id: str) -> None:
    task = runner._tasks.get(job_id)
    if task is not None:
        await task


async def test_mirror_copy_runs_after_backup_success(tmp_path) -> None:
    """C4 核心：备份成功后对镜像仓执行 copy，落 audit（ok）+ 任务日志 [copy]。"""
    db, runner, _fake, jobs, task = await _make_mirror_runner(tmp_path, mirror_ids=["mir"])
    job = await runner.submit("backup", task_id=task.id, trigger="scheduled")
    await _await(runner, job.id)

    final = await jobs.get(job.id)
    assert final is not None
    assert final.status == "success"

    log = Path(final.log_path).read_text(encoding="utf-8") if final.log_path else ""
    assert "[copy]" in log

    rows = await db.fetch_all(
        "SELECT action, detail, result FROM audit_logs WHERE action = ?", (AUDIT_COPY_RUN,)
    )
    assert len(rows) == 1
    assert str(rows[0]["result"]) == "ok"
    assert "mir" in str(rows[0]["detail"])
    await db.close()


async def test_mirror_copy_failure_does_not_fail_task(tmp_path) -> None:
    """镜像 copy 失败 → 主目的地仍 success；audit 标 failed；日志含失败原因。"""
    db, runner, _fake, jobs, task = await _make_mirror_runner(
        tmp_path, mirror_ids=["mir"], copy_exit=1
    )
    job = await runner.submit("backup", task_id=task.id, trigger="scheduled")
    await _await(runner, job.id)

    final = await jobs.get(job.id)
    assert final is not None
    assert final.status == "success"  # 主仓数据已落好，镜像失败不谎报

    rows = await db.fetch_all(
        "SELECT result FROM audit_logs WHERE action = ?", (AUDIT_COPY_RUN,)
    )
    assert len(rows) == 1
    assert str(rows[0]["result"]) == "failed"
    await db.close()


async def test_mirror_self_is_skipped_gracefully(tmp_path) -> None:
    """镜像仓=源仓（default）→ 跳过，不产生失败审计；日志标注跳过。"""
    db, runner, _fake, jobs, task = await _make_mirror_runner(tmp_path, mirror_ids=["default"])
    job = await runner.submit("backup", task_id=task.id, trigger="scheduled")
    await _await(runner, job.id)

    final = await jobs.get(job.id)
    assert final is not None
    assert final.status == "success"

    rows = await db.fetch_all(
        "SELECT result FROM audit_logs WHERE action = ?", (AUDIT_COPY_RUN,)
    )
    assert len(rows) == 1
    assert str(rows[0]["result"]) == "ok"  # 跳过是配置口径，不算执行失败
    await db.close()
