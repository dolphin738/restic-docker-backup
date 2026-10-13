"""ResticCmd 白名单回归（§7.1 / S2）：--include 拼装与越界、verb 白名单、值安全校验。

S2 红线：restic 参数只在 ``ResticCmd.build`` 白名单拼装——本文件是 ``--include``
（B1 文件级恢复 R-027）与既有白名单行为的断言落点。路径越界错误取
``PathOutOfPrefixError``（400 PATH_OUT_OF_PREFIX，PathMapper 原生错误类型）。
"""

from __future__ import annotations

import pytest

from app.errors import PathOutOfPrefixError, ValidationError
from app.services.path_mapper import PathMapper
from app.services.restic import ResticCmd, WhitelistArgs
from app.settings import Settings

SNAPSHOT_ID = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2"


def make_cmd(mode: str = "host") -> ResticCmd:
    """构造命令器（host 模式：模式根 /host）。"""
    settings = Settings(data_dir="/tmp/restic-whitelist", restic_web_mode=mode)
    return ResticCmd(settings, PathMapper(settings))


# ---------------------------------------------------------------- B1 --include（R-027）

def test_restore_include_assembled_in_order():
    """--include 逐值拼装（目录含子树、文件精确匹配），顺序与值形态正确。

    顺序：全局 flag → verb → verb flag（--target/--include/--overwrite）→ 位置参数；
    ``--overwrite=if-changed`` 由 JobRunner 固定注入（S7：界面无覆盖入口）。
    """
    cmd = make_cmd()
    argv = cmd.build(
        "restore",
        args=WhitelistArgs(
            snapshot_id=SNAPSHOT_ID,
            target_dir="/restore/etc-20261010-0138",
            include=("/host/etc", "/host/home/u/x"),
            overwrite="if-changed",
        ),
    )
    assert argv[1:] == [
        "--json",
        "--cache-dir",
        "/cache/restic",
        "restore",
        "--target",
        "/restore/etc-20261010-0138",
        "--include",
        "/host/etc",
        "--include",
        "/host/home/u/x",
        "--overwrite=if-changed",
        SNAPSHOT_ID,
    ]


def test_restore_without_include_has_no_flag():
    """缺省/空 include = 现有整快照行为，回归零影响。"""
    cmd = make_cmd()
    argv = cmd.build(
        "restore", args=WhitelistArgs(snapshot_id=SNAPSHOT_ID, target_dir="/restore/x")
    )
    assert "--include" not in argv


def test_restore_include_out_of_mode_root_rejected():
    """越界 include（跨模式/非模式根）→ PathOutOfPrefixError（S1 校验点②，纵深防御）。"""
    cmd = make_cmd("host")
    for bad in ("/restore/etc", "/sources/etc", "/etc"):
        with pytest.raises(PathOutOfPrefixError):
            cmd.build(
                "restore",
                args=WhitelistArgs(
                    snapshot_id=SNAPSHOT_ID, target_dir="/restore/x", include=(bad,)
                ),
            )


def test_restore_include_respects_sources_mode_root():
    """sources 模式下白名单前缀随模式根切换（/sources 合法、/host 越界）。"""
    cmd = make_cmd("sources")
    argv = cmd.build(
        "restore",
        args=WhitelistArgs(
            snapshot_id=SNAPSHOT_ID, target_dir="/restore/x", include=("/sources/etc",)
        ),
    )
    assert "--include" in argv
    assert argv[argv.index("--include") + 1] == "/sources/etc"

    with pytest.raises(PathOutOfPrefixError):
        cmd.build(
            "restore",
            args=WhitelistArgs(
                snapshot_id=SNAPSHOT_ID, target_dir="/restore/x", include=("/host/etc",)
            ),
        )


# ---------------------------------------------------------------- 既有白名单行为（防回归）

def test_verb_not_in_whitelist_rejected():
    cmd = make_cmd()
    with pytest.raises(ValidationError):
        cmd.build("rm", args=WhitelistArgs())


def test_exclude_flag_injection_rejected():
    """以 '-' 开头的值被拒（禁止注入 flag）。"""
    cmd = make_cmd()
    with pytest.raises(ValidationError):
        cmd.build("backup", args=WhitelistArgs(targets=("/host/etc",), excludes=("--evil",)))


def test_backup_target_out_of_prefix_rejected():
    """备份源位置参数越出三前缀白名单（/host /sources /restore 之外）→ 校验失败。"""
    cmd = make_cmd("host")
    with pytest.raises(PathOutOfPrefixError):
        cmd.build("backup", args=WhitelistArgs(targets=("/repo/etc",)))


# ---------------------------------------------------------------- R-016 forget --dry-run / --prune

def test_forget_dry_run_and_prune_assembled():
    """E21 预览/确认执行的 forget flag 拼装：keep-* + group-by + --dry-run/--prune。"""
    cmd = make_cmd()
    argv = cmd.build(
        "forget",
        args=WhitelistArgs(
            tags=("task-1",),
            group_by="host,paths,tags",
            keep_daily=7,
            keep_weekly=4,
            dry_run=True,
        ),
    )
    assert "--dry-run" in argv
    assert "--prune" not in argv  # 预览恒只读
    idx = argv.index("forget")
    assert argv[idx + 1: idx + 3] == ["--tag", "task-1"]
    assert argv[argv.index("--keep-daily") + 1] == "7"
    assert argv[argv.index("--group-by") + 1] == "host,paths,tags"


def test_forget_prune_only_when_confirmed():
    """--prune 只在执行参数显式携带时拼入（确认后的 E12 type=forget）。"""
    cmd = make_cmd()
    argv = cmd.build(
        "forget",
        args=WhitelistArgs(keep_daily=7, prune=True),
    )
    assert "--prune" in argv
    assert "--dry-run" not in argv


def test_forget_defaults_have_no_dry_run_or_prune():
    """既有 forget（调度/手动，未传 prune）行为零回归：两个 flag 都不出现。"""
    cmd = make_cmd()
    argv = cmd.build("forget", args=WhitelistArgs(keep_last=3))
    assert "--dry-run" not in argv
    assert "--prune" not in argv
