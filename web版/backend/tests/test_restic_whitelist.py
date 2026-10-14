"""ResticCmd 白名单回归（§7.1 / S2）：--include 拼装与越界、verb 白名单、值安全校验。

S2 红线：restic 参数只在 ``ResticCmd.build`` 白名单拼装——本文件是 ``--include``
（B1 文件级恢复 R-027）与既有白名单行为的断言落点。路径越界错误取
``PathOutOfPrefixError``（400 PATH_OUT_OF_PREFIX，PathMapper 原生错误类型）。
"""

from __future__ import annotations

import pytest

from app.constants import INPLACE_RESTORE_TARGET_ROOT
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


# ---------------------------------------------------------------- R-017 check --read-data / --read-data-subset

def test_check_read_data_assembled():
    """--read-data 具名字段拼装（既有行为，防回归）。"""
    cmd = make_cmd()
    argv = cmd.build("check", args=WhitelistArgs(read_data=True))
    assert "--read-data" in argv
    assert "--read-data-subset" not in argv


def test_check_defaults_have_no_read_flags():
    """缺省 check（纯元数据）不带任何读数据 flag。"""
    cmd = make_cmd()
    argv = cmd.build("check", args=WhitelistArgs())
    assert "--read-data" not in argv
    assert "--read-data-subset" not in argv


@pytest.mark.parametrize(
    "subset",
    [
        "1/4",  # 分数：n ≤ t
        "256/256",  # 分数：t 上限（totalBucketsMax）
        "50%",  # 比例
        "2.5%",  # 比例（小数）
        "100%",  # 比例上界
        "1G",  # 字节大小
        "100m",  # 字节大小（小写后缀）
        "512K",  # 字节大小（大写后缀）
        "1.5T",  # 字节大小（小数）
    ],
)
def test_check_read_data_subset_valid_forms_assembled(subset):
    """三形态合法值原样透传拼装（§7.1 V4 事实 3：校验在拼装层、形态不改动）。"""
    cmd = make_cmd()
    argv = cmd.build("check", args=WhitelistArgs(read_data_subset=subset))
    idx = argv.index("--read-data-subset")
    assert argv[idx + 1] == subset
    assert "--read-data" not in argv


@pytest.mark.parametrize(
    "subset",
    [
        "abc",  # 三形态都不匹配
        "1x",  # 未知后缀
        "-5",  # 负数（会被解析为 flag，S2 注入面）
        "0%",  # 比例必须 > 0
        "100.1%",  # 比例必须 ≤ 100
        "inf%",  # 非有限数
        "0/4",  # 分子必须 > 0
        "5/4",  # n > t
        "1/257",  # t 超上限 256
        "a/b",  # 分数非数字
    ],
)
def test_check_read_data_subset_invalid_rejected(subset):
    """非法形态 422（WhitelistArgs 层拒绝——restic 端 Fatal 无 JSON，无执行层兜底）。

    空串与 ``None`` 同语义（不加 flag，由 JobRunner 归一为 None），不在非法矩阵内。
    """
    cmd = make_cmd()
    with pytest.raises(ValidationError):
        cmd.build("check", args=WhitelistArgs(read_data_subset=subset))


def test_check_read_data_and_subset_mutually_exclusive():
    """``--read-data`` 与 ``--read-data-subset`` 互斥（restic checkFlags 同口径）。"""
    cmd = make_cmd()
    with pytest.raises(ValidationError):
        cmd.build("check", args=WhitelistArgs(read_data=True, read_data_subset="50%"))


# ---------------------------------------------------------------- R-015 原位恢复落点（S7 v1.3）

def test_restore_inplace_target_is_root_constant():
    """原位开关打开且 target 取 INPLACE_RESTORE_TARGET_ROOT → argv 拼出 ``--target /``。

    restic 落点 = ``--target`` + 快照内绝对路径，快照存容器路径 ``/host/etc/...``，
    故只有 ``/`` 是真原位（写到 ``/host/etc/...``）。
    """
    cmd = make_cmd()
    argv = cmd.build(
        "restore",
        args=WhitelistArgs(
            snapshot_id=SNAPSHOT_ID,
            target_dir=INPLACE_RESTORE_TARGET_ROOT,
            inplace_target=True,
            include=("/host/etc",),
            overwrite="always",
        ),
    )
    assert argv[argv.index("--target") + 1] == INPLACE_RESTORE_TARGET_ROOT
    assert INPLACE_RESTORE_TARGET_ROOT == "/"


def test_restore_inplace_switch_cannot_smuggle_other_target():
    """放行开关只能放行常量本身：夹带别的值 → 422（防"借布尔开关传任意路径"）。"""
    cmd = make_cmd()
    with pytest.raises(ValidationError):
        cmd.build(
            "restore",
            args=WhitelistArgs(
                snapshot_id=SNAPSHOT_ID, target_dir="/host/etc", inplace_target=True
            ),
        )


def test_restore_regular_target_still_validated_by_path_mapper():
    """常规恢复（时间戳子目录）的 --target 仍走 PathMapper 白名单（越界 400）。"""
    cmd = make_cmd()
    argv = cmd.build(
        "restore", args=WhitelistArgs(snapshot_id=SNAPSHOT_ID, target_dir="/restore/x")
    )
    assert argv[argv.index("--target") + 1] == "/restore/x"

    with pytest.raises(PathOutOfPrefixError):
        cmd.build("restore", args=WhitelistArgs(snapshot_id=SNAPSHOT_ID, target_dir="/etc"))
