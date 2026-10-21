"""R-023 收口护栏：调度维护 check 必须覆盖全部已就绪仓库（不再只查默认仓库）。

回归背景：原 ``SchedulerService._fire_check`` 提交 check 时不带 repo_id，落到
DEFAULT_REPO_ID，导致非默认仓库的定期校验（每日抽样 + 月度全量）从不执行。
本批改为遍历 ``JobRunner.list_repos()`` 逐仓库提交，未就绪仓库跳过。
"""
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


class _Config:
    async def get_value(self, key: str):
        return {
            "behavior.check_enabled": "1",
            "behavior.check_sample_ratio": "0.02",
            "behavior.monthly_full_day": "15",
        }.get(key)


def _make_scheduler(runner: _Runner, config: _Config) -> SchedulerService:
    return SchedulerService(
        tasks=AsyncMock(),
        runner=runner,
        config=config,
        settings=SimpleNamespace(tz="UTC"),
        jobs=AsyncMock(),
        retention=AsyncMock(),
    )


async def test_sample_check_covers_all_configured_repos(monkeypatch):
    repos = [_Repo("default"), _Repo("r2"), _Repo("r3")]
    runner = _Runner(repos)
    monkeypatch.setattr(
        scheduler_mod, "passfile_status", lambda _s, _rid: PASSFILE_STATUS_CONFIGURED
    )
    await _make_scheduler(runner, _Config())._fire_check("sample")
    assert [r[1] for r in runner.submitted] == ["default", "r2", "r3"]
    assert runner.submitted[0][3].get("read_data_subset") == "2%"  # 0.02 → 2%


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
    monkeypatch.setattr(
        scheduler_mod, "passfile_status", lambda _s, _rid: PASSFILE_STATUS_CONFIGURED
    )
    await _make_scheduler(runner, _Config())._fire_check("full")
    assert [r[1] for r in runner.submitted] == ["default", "r2"]
    assert all(r[3].get("read_data") is True for r in runner.submitted)
