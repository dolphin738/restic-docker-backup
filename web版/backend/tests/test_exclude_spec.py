"""批次② 过滤条件后端单测：glob 校验、! 前缀修复、ExcludeSpec 展平、WhitelistArgs 拼装。

对应设计方案 §2.2–§2.5；探针结论见 docs/源数据过滤与树双模式设计方案.md §2.4
（negate ∧ icase 禁用：--exclude 负向与 --iexclude 跨 flag 互不生效）。
"""

from __future__ import annotations

import pytest

from app.errors import ValidationError
from app.exclusions import normalize_user_excludes
from app.services.exclude_spec import (
    ExcludeRule,
    ExcludeSpec,
    QuickExcludes,
    flatten_exclude_spec,
    validate_glob,
    validate_size,
)
from app.services.path_mapper import PathMapper
from app.services.restic import ResticCmd, WhitelistArgs
from app.settings import Settings


def _docker_mapper() -> PathMapper:
    return PathMapper(Settings(deploy_mode="docker", sources_root="/sources", data_dir="/tmp/x"))


def _build(argv: list[str], flag: str) -> list[str]:
    """取某 flag 的全部取值（多值）。"""
    out: list[str] = []
    for i, token in enumerate(argv):
        if token == flag and i + 1 < len(argv):
            out.append(argv[i + 1])
    return out


# ---------------------------------------------------------------- glob 校验（缺陷①）


@pytest.mark.parametrize(
    "pattern",
    ["*.tmp", "**/*.log", "/sources/**/*.bak", "node_modules", "a?c", "[a-z]*.txt", "a\\*b"],
)
def test_validate_glob_accepts_valid(pattern: str):
    assert validate_glob(pattern) == pattern


@pytest.mark.parametrize("pattern", ["[abc", "a[]b", "{a,b}", "", "   ", "\x00x"])
def test_validate_glob_rejects_invalid(pattern: str):
    with pytest.raises(ValidationError):
        validate_glob(pattern)


def test_validate_size_accepts_and_rejects():
    for ok in ("100M", "1g", "2048", "1.5g", "10K"):
        assert validate_size(ok) == ok
    for bad in ("", "abc", "100MB", "-1M", "10 M"):
        with pytest.raises(ValidationError):
            validate_size(bad)


# ---------------------------------------------------------------- ExcludeSpec 展平


def test_flatten_quick_generates_anchored_patterns():
    spec = ExcludeSpec(
        quick=QuickExcludes(
            extensions=("tmp", ".log"),
            name_contains="cache",
            larger_than="100M",
            if_present=(".nobackup", "Thumbs.db"),
            caches=True,
        )
    )
    flat = flatten_exclude_spec(spec, _docker_mapper())
    assert flat.excludes == ("**/*.tmp", "**/*.log", "**/*cache*")
    assert flat.larger_than == "100M"
    assert flat.if_present == (".nobackup", "Thumbs.db")
    assert flat.caches is True


def test_flatten_rules_route_exclude_and_iexclude():
    spec = ExcludeSpec(
        rules=(
            ExcludeRule(pattern="*.bak"),  # 普通 → --exclude
            ExcludeRule(pattern="*.TMP", icase=True),  # icase → --iexclude
            ExcludeRule(pattern="keep.bak", negate=True),  # 负向 → --exclude（展平时加 !）
        )
    )
    flat = flatten_exclude_spec(spec, _docker_mapper())
    assert flat.excludes == ("*.bak", "!keep.bak")
    assert flat.iexcludes == ("*.TMP",)


def test_flatten_negative_rule_anchors_to_source_root():
    """以 / 开头的锚定规则经 to_snapshot 归一为容器形态（与存量排除同口径）。"""
    spec = ExcludeSpec(rules=(ExcludeRule(pattern="/etc/*.conf"),))
    flat = flatten_exclude_spec(spec, _docker_mapper())
    assert flat.excludes == ("/sources/etc/*.conf",)


def test_flatten_rejects_negate_with_icase():
    """negate ∧ icase 禁用（探针：跨 flag 负向无效）。"""
    spec = ExcludeSpec(rules=(ExcludeRule(pattern="*.log", negate=True, icase=True),))
    with pytest.raises(ValidationError):
        flatten_exclude_spec(spec, _docker_mapper())


def test_flatten_rejects_pattern_with_bang():
    spec = ExcludeSpec(rules=(ExcludeRule(pattern="!*.log"),))
    with pytest.raises(ValidationError):
        flatten_exclude_spec(spec, _docker_mapper())


def test_flatten_rejects_bad_glob():
    spec = ExcludeSpec(quick=QuickExcludes(extensions=("",)), rules=(ExcludeRule(pattern="[abc"),))
    with pytest.raises(ValidationError):
        flatten_exclude_spec(spec, _docker_mapper())


# ---------------------------------------------------------------- ! 前缀修复（缺陷②）


def test_normalize_negative_anchored_rule_gets_prefix():
    """!/etc → 剥 ! 锚定 /sources/etc，再拼回 !（修静默失效）。"""
    mapper = _docker_mapper()
    out = normalize_user_excludes(["!/etc"], mapper, source_prefixes=("/sources",))
    assert out == ["!/sources/etc"]


def test_normalize_negative_pure_glob_preserved():
    mapper = _docker_mapper()
    out = normalize_user_excludes(["!*.log"], mapper, source_prefixes=("/sources",))
    assert out == ["!*.log"]


def test_normalize_rejects_bad_glob():
    mapper = _docker_mapper()
    with pytest.raises(ValidationError):
        normalize_user_excludes(["/sources/[abc"], mapper, source_prefixes=("/sources",))


# ---------------------------------------------------------------- WhitelistArgs 拼装


def test_whitelist_args_emit_new_filter_flags():
    settings = Settings(deploy_mode="docker", sources_root="/sources", data_dir="/tmp/x")
    cmd = ResticCmd(settings, PathMapper(settings))
    argv = cmd.build(
        "backup",
        args=WhitelistArgs(
            targets=("/sources",),
            iexcludes=("*.LOG",),
            exclude_larger_than="100M",
            exclude_if_present=(".nobackup", "Thumbs.db"),
            exclude_caches=True,
        ),
    )
    assert _build(argv, "--iexclude") == ["*.LOG"]
    assert _build(argv, "--exclude-larger-than") == ["100M"]
    assert _build(argv, "--exclude-if-present") == [".nobackup", "Thumbs.db"]
    assert "--exclude-caches" in argv


def test_whitelist_args_rejects_bad_size():
    settings = Settings(deploy_mode="docker", sources_root="/sources", data_dir="/tmp/x")
    cmd = ResticCmd(settings, PathMapper(settings))
    with pytest.raises(ValidationError):
        cmd.build("backup", args=WhitelistArgs(targets=("/sources",), exclude_larger_than="abc"))
