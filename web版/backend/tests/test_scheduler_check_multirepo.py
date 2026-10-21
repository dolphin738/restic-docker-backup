"""R-023 收口护栏：调度维护 check 必须覆盖全部已就绪仓库（不再只查默认仓库）。

回归背景：原 ``SchedulerService._fire_check`` 提交 check 时不带 repo_id，落到
DEFAULT_REPO_ID，导致非默认仓库的定期校验（每日抽样 + 月度全量）从不执行。
本批改为遍历 ``JobRunner.list_repos()`` 逐仓库提交，未就绪仓库跳过。

2026-10-21 追加（校验策略收口）：每日抽样由**随机** ``x%`` 改为 **n/t 确定性分桶**
——t 天一轮恰好把全仓数据块无重复、无遗漏读一遍（随机形态官方明示不保证覆盖）；
月度全量新增独立开关 ``behavior.monthly_full_enabled``（缺省关）。本文件新增
这两项语义的护栏（含「一轮遍历全部分桶」「月度全量当天跳过抽样」等性质测试）。
"""
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.constants import PASSFILE_STATUS_CONFIGURED, PASSFILE_STATUS_MISSING
from app.services import scheduler as scheduler_mod
from app.services.scheduler import SchedulerService


class _Repo:
    def __init__(self, id: str) -> None:
        self.id = id


class _Runner:
    def __init__(self, repos: list[_Repo]) -> None:
        self._repos = repos
        self.submitted: list[tuple[str, str, str, dict]] = []

    async def list_repos(self) -> list[_Repo]:
        return list(self._repos)

    async def submit(self, job_type, *, repo_id="default", trigger="manual", params=None, **_kw):
        self.submitted.append((job_type, repo_id, trigger, dict(params or {})))


_DEFAULT_VALUES: dict[str, str] = {
    "behavior.check_enabled": "1",
    "behavior.check_subset_denominator": "30",
    "behavior.monthly_full_day": "15",
    "behavior.monthly_full_enabled": "1",
}


class _Config:
    def __init__(self, values: dict[str, str] | None = None) -> None:
        self._values = dict(_DEFAULT_VALUES if values is None else values)

    async def get_value(self, key: str):
        return self._values.get(key)


def _make_scheduler(runner: _Runner, config: _Config) -> SchedulerService:
    return SchedulerService(
        tasks=AsyncMock(),
        runner=runner,
        config=config,
        settings=SimpleNamespace(tz="UTC"),
        jobs=AsyncMock(),
        retention=AsyncMock(),
    )


def _all_ready(monkeypatch) -> None:
    monkeypatch.setattr(
        scheduler_mod, "passfile_status", lambda _s, _rid: PASSFILE_STATUS_CONFIGURED
    )


async def test_sample_check_covers_all_configured_repos(monkeypatch):
    repos = [_Repo("default"), _Repo("r2"), _Repo("r3")]
    runner = _Runner(repos)
    _all_ready(monkeypatch)
    await _make_scheduler(runner, _Config())._fire_check("sample")
    assert [r[1] for r in runner.submitted] == ["default", "r2", "r3"]
    subset = str(runner.submitted[0][3].get("read_data_subset"))
    assert subset.endswith("/30")  # t = behavior.check_subset_denominator
    assert 1 <= int(subset.split("/")[0]) <= 30


async def test_check_skips_unconfigured_repos(monkeypatch):
    repos = [_Repo("default"), _Repo("r-no-pf")]
    runner = _Runner(repos)

    def fake_status(_s, repo_id):
        return PASSFILE_STATUS_CONFIGURED if repo_id == "default" else PASSFILE_STATUS_MISSING

    monkeypatch.setattr(scheduler_mod, "passfile_status", fake_status)
    await _make_scheduler(runner, _Config())._fire_check("sample")
    assert [r[1] for r in runner.submitted] == ["default"]


async def test_full_check_covers_all_repos(monkeypatch):
    repos = [_Repo("default"), _Repo("r2")]
    runner = _Runner(repos)
    _all_ready(monkeypatch)
    await _make_scheduler(runner, _Config())._fire_check("full")
    assert [r[1] for r in runner.submitted] == ["default", "r2"]
    assert all(r[3].get("read_data") is True for r in runner.submitted)


async def test_full_check_requires_monthly_full_switch(monkeypatch):
    """月度全量独立开关缺省关 → 即便配了 monthly_full_day，fire 也不提交（方案 A 默认态）。"""
    runner = _Runner([_Repo("default")])
    _all_ready(monkeypatch)
    values = {k: v for k, v in _DEFAULT_VALUES.items() if k != "behavior.monthly_full_enabled"}
    await _make_scheduler(runner, _Config(values))._fire_check("full")
    assert runner.submitted == []


async def test_sample_bucket_covers_all_buckets(monkeypatch):
    """核心性质：t 天内桶号恰好遍历 1..t 各一次（确定性分桶 = 零重复、零遗漏）。"""
    runner = _Runner([_Repo("default")])
    _all_ready(monkeypatch)
    total = 5
    scheduler = _make_scheduler(
        runner, _Config({**_DEFAULT_VALUES, "behavior.check_subset_denominator": str(total)})
    )
    base = date(2026, 3, 1)  # 避开 monthly_full_day=15，不触发跳过
    seen: set[int] = set()
    for offset in range(total):
        monkeypatch.setattr(
            SchedulerService, "_today_local", lambda self, _d=base + timedelta(days=offset): _d
        )
        await scheduler._fire_check("sample")
        numerator, denominator = str(runner.submitted[-1][3]["read_data_subset"]).split("/")
        assert denominator == str(total)
        seen.add(int(numerator))
    assert seen == set(range(1, total + 1))


async def test_sample_skipped_on_monthly_full_day(monkeypatch):
    """月度全量当天跳过每日抽样（全量已读遍全仓，当日抽样属纯重复读）。"""
    runner = _Runner([_Repo("default")])
    _all_ready(monkeypatch)
    monkeypatch.setattr(SchedulerService, "_today_local", lambda self: date(2026, 5, 15))
    await _make_scheduler(runner, _Config())._fire_check("sample")
    assert runner.submitted == []


async def test_sample_runs_when_monthly_full_off(monkeypatch):
    """反向断言：月度全量开关关闭时，同一天（15 日）抽样照常执行，不得被跳过。"""
    runner = _Runner([_Repo("default")])
    _all_ready(monkeypatch)
    monkeypatch.setattr(SchedulerService, "_today_local", lambda self: date(2026, 5, 15))
    cfg = _Config({**_DEFAULT_VALUES, "behavior.monthly_full_enabled": "0"})
    await _make_scheduler(runner, cfg)._fire_check("sample")
    assert [r[1] for r in runner.submitted] == ["default"]


async def test_legacy_ratio_falls_back_to_denominator(monkeypatch):
    """旧键 check_sample_ratio=0.02 且新键未设 → 按 t = round(1 / 0.02) = 50 兜底。"""
    runner = _Runner([_Repo("default")])
    _all_ready(monkeypatch)
    cfg = _Config({"behavior.check_enabled": "1", "behavior.check_sample_ratio": "0.02"})
    await _make_scheduler(runner, cfg)._fire_check("sample")
    assert str(runner.submitted[0][3]["read_data_subset"]).endswith("/50")
