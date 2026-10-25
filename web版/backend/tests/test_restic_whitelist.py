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


def make_cmd() -> ResticCmd:
    """构造命令器（A0 模式收敛后唯一形态：备份源根 /sources）。"""
    settings = Settings(data_dir="/tmp/restic-whitelist")
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
            include=("/sources/etc", "/sources/home/u/x"),
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
        "/sources/etc",
        "--include",
        "/sources/home/u/x",
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
    """越界 include（非备份源根 /sources）→ PathOutOfPrefixError（S1 校验点②，纵深防御）。

    /host 为已删除的旧模式前缀（A0），残留写法同样越界。
    """
    cmd = make_cmd()
    for bad in ("/restore/etc", "/host/etc", "/etc"):
        with pytest.raises(PathOutOfPrefixError):
            cmd.build(
                "restore",
                args=WhitelistArgs(
                    snapshot_id=SNAPSHOT_ID, target_dir="/restore/x", include=(bad,)
                ),
            )


def test_restore_include_respects_sources_mode_root():
    """白名单前缀随备份源根收敛（/sources 合法、/host 越界）。"""
    cmd = make_cmd()
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
        cmd.build("backup", args=WhitelistArgs(targets=("/sources/etc",), excludes=("--evil",)))


def test_backup_target_out_of_prefix_rejected():
    """备份源位置参数越出白名单（/sources /restore 之外）→ 校验失败。"""
    cmd = make_cmd()
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

    restic 落点 = ``--target`` + 快照内绝对路径，快照存容器路径 ``/sources/etc/...``，
    故只有 ``/`` 是真原位（写到 ``/sources/etc/...``）。
    """
    cmd = make_cmd()
    argv = cmd.build(
        "restore",
        args=WhitelistArgs(
            snapshot_id=SNAPSHOT_ID,
            target_dir=INPLACE_RESTORE_TARGET_ROOT,
            inplace_target=True,
            include=("/sources/etc",),
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
                snapshot_id=SNAPSHOT_ID, target_dir="/sources/etc", inplace_target=True
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


# ---------------------------------------------------------------- B1 自愈 repair（restic repair index / packs）

def test_repair_index_assembled_without_json():
    """repair index：两级命令拼装，且不加 --json（repair 输出非 JSON）。"""
    cmd = make_cmd()
    argv = cmd.build("repair", args=WhitelistArgs(repair_target="index", json_output=False))
    assert "--json" not in argv
    idx = argv.index("repair")
    assert argv[idx + 1] == "index"


def test_repair_packs_assembled():
    """repair packs：子命令 packs 透传。"""
    cmd = make_cmd()
    argv = cmd.build("repair", args=WhitelistArgs(repair_target="packs", json_output=False))
    assert argv[argv.index("repair") + 1] == "packs"


def test_repair_default_target_is_index():
    """缺省 repair_target → index（最常用、最安全）。"""
    cmd = make_cmd()
    argv = cmd.build("repair", args=WhitelistArgs(json_output=False))
    assert argv[argv.index("repair") + 1] == "index"


def test_repair_invalid_target_rejected():
    """非法 repair 子命令（非 index/packs）→ ValidationError（S2 白名单）。"""
    cmd = make_cmd()
    with pytest.raises(ValidationError):
        cmd.build("repair", args=WhitelistArgs(repair_target="drop", json_output=False))


def test_repair_and_recover_in_verb_whitelist():
    """B1/B3 自愈 verb 已入白名单（与 test_verb_not_in_whitelist_rejected 互补）。"""
    from app.services.restic import VERBS

    assert "repair" in VERBS
    assert "recover" in VERBS


def test_repair_in_job_types_and_enum():
    """repair 已入 JOB_TYPES 与 JobType 枚举（E12 校验口径）。"""
    from app.constants import JOB_TYPES
    from app.schemas.enums import JobType

    assert "repair" in JOB_TYPES
    assert JobType.REPAIR == "repair"


def test_recover_in_job_types_and_enum():
    """B3 自愈：recover 已入 JOB_TYPES 与 JobType 枚举（E12 校验口径）。"""
    from app.constants import JOB_TYPES
    from app.schemas.enums import JobType

    assert "recover" in JOB_TYPES
    assert JobType.RECOVER == "recover"


def test_recover_assembled_without_json():
    """B3 自愈：restic recover 拼装，不加 --json（输出为人类可读文本）。"""
    cmd = make_cmd()
    argv = cmd.build("recover", args=WhitelistArgs(json_output=False))
    assert "--json" not in argv
    assert "recover" in argv


# ---------------------------------------------------------------- E39 时区统一（子进程 TZ 收口）

def test_build_env_injects_settings_tz_as_initial():
    """构造期 TZ 回落链第一跳：restic.tz 初值 = settings.tz（compose 级回退）。"""
    settings = Settings(data_dir="/tmp/restic-whitelist", tz="Asia/Shanghai")
    cmd = ResticCmd(settings, PathMapper(settings))
    env = cmd.build_env(repo_url="/repo")
    assert env["TZ"] == "Asia/Shanghai"


def test_set_timezone_refreshes_subprocess_tz():
    """UI 改时区（config.timezone）后，restic 子进程 TZ 必须即时切换。

    旧实现直读 settings.tz（compose 级，运行期不可变）→ 用户改「系统设置-时区」
    对 restic 文本输出（snapshots 表格 / key list 的 created 等）不生效。
    """
    settings = Settings(data_dir="/tmp/restic-whitelist", tz="UTC")
    cmd = ResticCmd(settings, PathMapper(settings))
    cmd.set_timezone("Asia/Seoul")
    assert cmd.build_env(repo_url="/repo")["TZ"] == "Asia/Seoul"


def test_set_timezone_empty_or_none_falls_back_to_settings():
    """空值/None = 清除配置回落 settings.tz（与调度/系统时间接口同回退链）。"""
    settings = Settings(data_dir="/tmp/restic-whitelist", tz="Asia/Shanghai")
    cmd = ResticCmd(settings, PathMapper(settings))
    cmd.set_timezone("Asia/Seoul")
    cmd.set_timezone("")
    assert cmd.build_env(repo_url="/repo")["TZ"] == "Asia/Shanghai"
    cmd.set_timezone("Asia/Seoul")
    cmd.set_timezone(None)
    assert cmd.build_env(repo_url="/repo")["TZ"] == "Asia/Shanghai"
