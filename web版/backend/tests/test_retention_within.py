"""A2 forget 保留档位补齐回归：时长档（--keep-within*）+ 标签档（--keep-tag）。

覆盖四层（与实现同序）：
1. 拼装层：``_normalize_duration`` 形态校验（restic data.ParseDuration 语义 +
   本项目 stricter：重复单位拒绝、全零归一未启用）；WhitelistArgs forget argv 拼装；
2. 写侧：TaskService.normalize / to_api 归一（fail-fast 422）；
3. 读侧：TaskRepo 列往返（0004 新列缺省口径）；
4. 执行侧：JobRunner._build_args forget 分支 G3 三门（计数/时长/标签并列）+
   ops.require_keep_rule / build_forget_args 同口径。

restic 0.19.1 事实基线（源码核验）：duration = 若干 <整数><单位> 顺接，单位
y/m/d/h，任意顺序；重复单位 restic 后者覆盖（本项目 422）；--keep-tag 每次
一个 tag 列表（本项目每元素一条 flag，OR 语义）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from app.db.database import Database
from app.db.repositories.audit_repo import AuditRepo
from app.db.repositories.job_repo import JobRepo
from app.db.repositories.task_repo import TaskRepo
from app.errors import ValidationError
from app.routers.ops import build_forget_args, require_keep_rule
from app.services.job_runner import JobRunner
from app.services.parser import OutputParser
from app.services.path_mapper import PathMapper
from app.services.restic import ResticCmd, WhitelistArgs, _normalize_duration
from app.services.task_service import TaskService
from app.settings import Settings

# ---------------------------------------------------------------- 1. 拼装层：duration 校验


@pytest.mark.parametrize(
    "value",
    ["7d", "1y5m7d2h", "2h5y", "10d", "1m", "3y", "0d1h", "24h"],
)
def test_normalize_duration_accepts_valid_forms(value):
    """合法形态原样透传（任意顺序、可组合；restic 同款语义）。"""
    assert _normalize_duration(value, "keep_within") == value


@pytest.mark.parametrize("value", [None, "", "   ", "\t"])
def test_normalize_duration_empty_means_unset(value):
    """空/全空白 → None（未启用，不加 flag）。"""
    assert _normalize_duration(value, "keep_within") is None


@pytest.mark.parametrize("value", ["0d", "0y0m0d0h", "0h"])
def test_normalize_duration_all_zero_means_unset(value):
    """全零 → None：restic 零时长无约束力，按未启用处理（与 keep_* 的 0 口径一致）。"""
    assert _normalize_duration(value, "keep_within") is None


@pytest.mark.parametrize(
    "value",
    [
        "7",  # 无单位数字（restic：no unit found）
        "-7d",  # 负号（restic 解析接受但 verifyForgetOptions 拒；此处直接 422）
        "7w",  # 未知单位（restic duration 无周）
        "1.5d",  # 小数
        "d7",  # 缺数字
        "1y 5m",  # 内部空白
        "1y5m7d2h30m",  # 重复单位（restic 后者覆盖，语义歧义）
        "2y3y",  # 重复单位（同单位）
        "7D",  # 大小写敏感（restic 只认小写）
    ],
)
def test_normalize_duration_rejects_invalid_forms(value):
    """非法形态 422（拼装层前移：restic 端 Fatal 且零 JSON 产出，无兜底机会）。"""
    with pytest.raises(ValidationError) as exc_info:
        _normalize_duration(value, "keep_within")
    assert exc_info.value.data == {"field": "keep_within", "value": value}


def test_normalize_duration_error_names_field():
    """错误 data 带具体字段名（六档各自可定位）。"""
    with pytest.raises(ValidationError) as exc_info:
        _normalize_duration("bad", "keep_within_daily")
    assert exc_info.value.data["field"] == "keep_within_daily"


# ---------------------------------------------------------------- 2. 拼装层：forget argv


def make_cmd() -> ResticCmd:
    settings = Settings(data_dir="/tmp/restic-a2")
    return ResticCmd(settings, PathMapper(settings))


def test_forget_argv_assembles_within_and_keep_tags():
    """A2 新档位入 argv：--keep-within* 六档 + --keep-tag（每元素一条，OR 语义）。"""
    cmd = make_cmd()
    argv = cmd.build(
        "forget",
        args=WhitelistArgs(
            tags=("task-1",),
            group_by="host,paths,tags",
            keep_daily=7,
            keep_within="1y5m7d2h",
            keep_within_daily="30d",
            keep_within_monthly="1y",
            keep_tags=("important", "legal-hold"),
        ),
    )
    assert argv[1:] == [
        "--json",
        "--cache-dir",
        "/cache/restic",
        "forget",
        "--tag",
        "task-1",
        "--keep-daily",
        "7",
        "--keep-within",
        "1y5m7d2h",
        "--keep-within-daily",
        "30d",
        "--keep-within-monthly",
        "1y",
        "--keep-tag",
        "important",
        "--keep-tag",
        "legal-hold",
        "--group-by",
        "host,paths,tags",
    ]


def test_forget_argv_unset_within_adds_no_flag():
    """未启用档位（None/空串/全零）不出现在 argv——与 keep_* 的 0 口径一致。"""
    cmd = make_cmd()
    argv = cmd.build(
        "forget",
        args=WhitelistArgs(keep_last=3, keep_within="", keep_within_daily="0d", keep_tags=()),
    )
    assert not any(flag.startswith("--keep-within") for flag in argv)
    assert "--keep-tag" not in argv


def test_forget_argv_rejects_bad_duration_at_build():
    """拼装层兜底：绕过前端的坏 duration 在 build 时 422（不到 restic 端 Fatal）。"""
    cmd = make_cmd()
    with pytest.raises(ValidationError):
        cmd.build("forget", args=WhitelistArgs(keep_daily=7, keep_within="7"))


def test_forget_argv_rejects_keep_tag_flag_injection():
    """keep_tag 过 _assert_safe_value：禁 '-' 开头（flag 注入防护，与 --tag 同款）。"""
    cmd = make_cmd()
    with pytest.raises(ValidationError):
        cmd.build("forget", args=WhitelistArgs(keep_daily=7, keep_tags=("-evil",)))


# ---------------------------------------------------------------- 3. 写侧：TaskService 归一


def make_task_service(db: Database) -> TaskService:
    return TaskService(TaskRepo(db), Settings(data_dir="/tmp/restic-a2-task"))


def _base_payload() -> dict:
    """normalize 直调的最小合法载荷（API schema 缺省值在此显式给全）。"""
    return {
        "name": "每日备份",
        "sources": ["/etc"],
        "keep_daily": 7,
        "check_sample_ratio": 0.02,
        "monthly_full_day": 1,
    }


async def test_normalize_accepts_within_and_tags(tmp_path):
    """合法时长档 + 标签档归一落列（空档位归一空串/空列表）。"""
    db = Database(tmp_path / "t.db")
    await db.connect()
    service = make_task_service(db)
    values = service.normalize(
        {
            **_base_payload(),
            "keep_within": " 1y5m7d2h ",
            "keep_within_daily": "30d",
            "keep_tags": [" important ", "", "legal-hold", "important"],
        }
    )
    assert values["keep_within"] == "1y5m7d2h"  # 去首尾空白
    assert values["keep_within_daily"] == "30d"
    assert values["keep_tags"] == ["important", "legal-hold"]  # 去空白/丢空/保序去重
    assert values["keep_within_hourly"] == ""  # 未给 → 空串（未启用）
    await db.close()


async def test_normalize_rejects_bad_within(tmp_path):
    """写侧 fail-fast：坏 duration 保存即 422（Q4 静默防线——不留到提交时）。"""
    db = Database(tmp_path / "t.db")
    await db.connect()
    service = make_task_service(db)
    with pytest.raises(ValidationError) as exc_info:
        service.normalize({**_base_payload(), "keep_within_monthly": "1y5x"})
    assert exc_info.value.data["field"] == "keep_within_monthly"
    await db.close()


async def test_normalize_rejects_keep_tag_injection(tmp_path):
    """写侧 fail-fast：'-evil' 标签保存即 422（flag 注入防护前移）。"""
    db = Database(tmp_path / "t.db")
    await db.connect()
    service = make_task_service(db)
    with pytest.raises(ValidationError):
        service.normalize({**_base_payload(), "keep_tags": ["-evil"]})
    await db.close()


async def test_normalize_all_zero_within_becomes_empty(tmp_path):
    """全零时长写侧归一为空串（未启用）——库存值不变式，调度器预检依赖此不变量。"""
    db = Database(tmp_path / "t.db")
    await db.connect()
    service = make_task_service(db)
    values = service.normalize({**_base_payload(), "keep_within": "0d"})
    assert values["keep_within"] == ""
    await db.close()


async def test_to_api_exposes_new_fields(tmp_path):
    """读侧换算透出七新字段（keep_within* 串 + keep_tags 列表）。"""
    db = Database(tmp_path / "t.db")
    await db.connect()
    repo = TaskRepo(db)
    task = await repo.create(
        {
            "name": "t",
            "sources": ["/sources/srv"],
            "keep_within": "7d",
            "keep_within_yearly": "1y",
            "keep_tags": ["important"],
        }
    )
    api = make_task_service(db).to_api(task)
    assert api["keep_within"] == "7d"
    assert api["keep_within_yearly"] == "1y"
    assert api["keep_tags"] == ["important"]
    assert api["keep_within_hourly"] == ""
    await db.close()


# ---------------------------------------------------------------- 4. 读侧：TaskRepo 往返


async def test_task_repo_round_trip_new_columns(tmp_path):
    """0004 新列往返：create → get 原样读回；get_dict（profile）同键透出。"""
    db = Database(tmp_path / "t.db")
    await db.connect()
    repo = TaskRepo(db)
    created = await repo.create(
        {
            "name": "round",
            "sources": ["/sources/srv"],
            "keep_within": "2y5m7d3h",
            "keep_within_weekly": "90d",
            "keep_tags": ["a", "b"],
        }
    )
    assert created.keep_within == "2y5m7d3h"
    assert created.keep_within_weekly == "90d"
    assert created.keep_tags == ["a", "b"]

    fetched = await repo.get(created.id)
    assert fetched is not None
    assert fetched.keep_within == "2y5m7d3h"
    assert fetched.keep_tags == ["a", "b"]

    profile = await repo.get_dict(created.id)
    assert profile is not None
    assert profile["keep_within"] == "2y5m7d3h"
    assert profile["keep_within_weekly"] == "90d"
    assert profile["keep_tags"] == ["a", "b"]
    await db.close()


async def test_task_repo_defaults_unset(tmp_path):
    """缺省口径：不显式给新档位 → 空串/空列表（未启用）。"""
    db = Database(tmp_path / "t.db")
    await db.connect()
    repo = TaskRepo(db)
    task = await repo.create({"name": "plain", "sources": ["/sources/srv"]})
    assert task.keep_within == ""
    assert task.keep_within_yearly == ""
    assert task.keep_tags == []
    await db.close()


# ---------------------------------------------------------------- 5. 执行侧：JobRunner G3 三门


@dataclass
class _FakeProcess:
    exit_code: int = 0

    log_path: Path = Path("/tmp/x.log")

    async def lines(self):  # noqa: ANN202
        return
        yield  # pragma: no cover - 空生成器

    async def wait(self) -> int:
        return self.exit_code

    def terminate(self) -> None:  # pragma: no cover - 本文件不触发
        pass

    def kill(self) -> None:  # pragma: no cover - 本文件不触发
        pass


@dataclass
class _FakeRestic:
    """ResticCmd 替身：记录最近一次 build 的 WhitelistArgs（不跑真进程）。"""

    last_args: WhitelistArgs | None = field(default=None)

    def build(self, verb, *, args, env_extras=None):  # noqa: ANN001, ANN202
        self.last_args = args
        return ["restic-fake", verb]

    def build_env(self, *, repo_url, repo_id="default", env_extras=None):  # noqa: ANN001, ANN202
        return {}

    async def spawn(self, argv, log_path, *, env=None):  # noqa: ANN001, ANN202
        return _FakeProcess(log_path=Path(log_path))


async def make_runner(tmp_path) -> tuple[Database, JobRunner, JobRepo, TaskRepo]:
    db = Database(tmp_path / "jobs.db")
    await db.connect()
    settings = Settings(data_dir=str(tmp_path))
    settings.jobs_dir.mkdir(parents=True, exist_ok=True)
    task_repo = TaskRepo(db)
    fake = _FakeRestic()
    runner = JobRunner(
        db,
        fake,  # type: ignore[arg-type]
        OutputParser(),
        AuditRepo(db),
        settings=settings,
        task_loader=task_repo.get_dict,
    )
    runner._fake = fake  # type: ignore[attr-defined]
    return db, runner, JobRepo(db), task_repo


async def await_job(runner: JobRunner, job_id: str) -> None:
    task = runner._tasks.get(job_id)
    if task is not None:
        await task


async def test_forget_within_only_passes_g3(tmp_path):
    """G3 三门并列：仅 keep_within（无任何计数档）→ 正常执行，args 透传。"""
    db, runner, jobs, _tasks = await make_runner(tmp_path)
    job = await runner.submit(
        "forget", trigger="manual", params={"tags": ["task-1"], "keep_within": "30d"}
    )
    await await_job(runner, job.id)
    final = await jobs.get(job.id)
    assert final is not None and final.status == "success"
    args = runner._fake.last_args  # type: ignore[attr-defined]
    assert args is not None and args.keep_within == "30d"
    assert args.keep_daily is None
    await db.close()


async def test_forget_keep_tags_only_passes_g3(tmp_path):
    """G3 三门并列：仅 keep_tags → 正常执行，args.keep_tags 透传。"""
    db, runner, jobs, _tasks = await make_runner(tmp_path)
    job = await runner.submit(
        "forget", trigger="manual", params={"tags": ["task-1"], "keep_tags": ["important"]}
    )
    await await_job(runner, job.id)
    final = await jobs.get(job.id)
    assert final is not None and final.status == "success"
    args = runner._fake.last_args  # type: ignore[attr-defined]
    assert args is not None and args.keep_tags == ("important",)
    await db.close()


async def test_forget_no_rule_at_all_fails_job(tmp_path):
    """G3：计数/时长/标签全空 → 执行期 ValidationError → job failed（全删语义拒死）。"""
    db, runner, jobs, _tasks = await make_runner(tmp_path)
    job = await runner.submit(
        "forget", trigger="manual", params={"tags": ["task-1"], "keep_daily": 0, "keep_within": ""}
    )
    await await_job(runner, job.id)
    final = await jobs.get(job.id)
    assert final is not None and final.status == "failed"
    assert "ValidationError" in (final.summary or "")
    await db.close()


async def test_forget_bad_duration_fails_job(tmp_path):
    """执行侧兜底：params 直带坏 duration（绕过预览回显）→ 拼装层 422 → job failed。"""
    db, runner, jobs, _tasks = await make_runner(tmp_path)
    job = await runner.submit(
        "forget", trigger="manual", params={"tags": ["task-1"], "keep_within": "7"}
    )
    await await_job(runner, job.id)
    final = await jobs.get(job.id)
    assert final is not None and final.status == "failed"
    await db.close()


async def test_forget_profile_within_fallback(tmp_path):
    """profile 回退：params 未给时长档 → 取任务 profile 的 keep_within*（任务级策略生效）。"""
    db, runner, jobs, task_repo = await make_runner(tmp_path)
    task = await task_repo.create(
        {
            "name": "within-task",
            "sources": ["/sources/srv"],
            "keep_within": "90d",
            "keep_tags": ["important"],
        }
    )
    job = await runner.submit("forget", task_id=task.id, trigger="manual", params={"prune": True})
    await await_job(runner, job.id)
    final = await jobs.get(job.id)
    assert final is not None and final.status == "success"
    args = runner._fake.last_args  # type: ignore[attr-defined]
    assert args is not None
    assert args.keep_within == "90d"
    assert args.keep_tags == ("important",)
    assert args.prune is True
    await db.close()


# ---------------------------------------------------------------- 6. ops：G3 门与映射（单元）


def test_require_keep_rule_accepts_within_only():
    """E21 预览门：仅时长档（无计数档）放行（restic ExpirePolicy.Empty 含 Within）。"""
    require_keep_rule(None, None, None, None, None, None, "30d", None, None, None, None, None, ())


def test_require_keep_rule_accepts_tags_only():
    """E21 预览门：仅标签档放行。"""
    require_keep_rule(None, None, None, None, None, None, None, None, None, None, None, None, ["x"])


def test_require_keep_rule_rejects_all_empty():
    """E21 预览门：全空 422，data.keep_fields 列全部门类（含 A2 三门）。"""
    with pytest.raises(ValidationError) as exc_info:
        require_keep_rule(None, None, None, None, None, None, None, None, None, None, None, None, ())
    fields = exc_info.value.data["keep_fields"]
    assert "keep_daily" in fields
    assert "keep_within" in fields
    assert "keep_tags" in fields


def test_build_forget_args_maps_all_fields():
    """build_forget_args 全字段映射（计数/时长/标签 → WhitelistArgs 具名）。"""
    args = build_forget_args(
        tag="task-1",
        group_by="host,paths,tags",
        keep={
            "keep_last": 3,
            "keep_within": "30d",
            "keep_within_yearly": "1y",
            "keep_tags": ["important", "legal"],
        },
        dry_run=True,
        prune=False,
    )
    assert args.keep_last == 3
    assert args.keep_within == "30d"
    assert args.keep_within_yearly == "1y"
    assert args.keep_tags == ("important", "legal")
    assert args.tags == ("task-1",)
    assert args.dry_run is True
    assert args.prune is False
