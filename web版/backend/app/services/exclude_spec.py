"""ExcludeSpec（设计方案 §2.2–§2.4）：快捷排除卡 + 有序规则列表 → restic 过滤参数。

单一实现点，承担四件事：

1. **glob 语法校验**——restic 对非法 pattern（如未闭合的 ``[abc``）**Fatal exit 1**，
   会打死整个备份；本模块在入库/拼装前 422 前置拦截（修存量缺陷①）。
2. **``!`` 负向语义**——负向只在本模块以 ``negate`` 布尔表达，展平时再拼 ``!`` 前缀；
   禁止 pattern 自带 ``!``（避免双重否定歧义）。
3. **negate × icase 禁用**——restic 0.18.0 实测（见设计方案探针表）：
   ``--iexclude '!X'`` 只在 ``--iexclude`` 族内生效，**跨 flag 的负向无效**
   （``--exclude '!X'`` 无法取消 ``--iexclude`` 命中的正向，反之亦然）。
   故 ``negate=True 且 icase=True`` 直接 422；UI 负向行隐藏 icase 开关。
4. **展平顺序**——quick 生成的 pattern 在前、rules 按列表序在后（保证负向能取消
   快捷卡的宽规则）；``negate=False ∧ icase=True`` 进 ``--iexclude``，其余进 ``--exclude``。

与 ``exclusions.normalize_user_excludes`` 的分工：后者负责「展示形态 ↔ 容器形态」的
前缀换算（PathMapper）；本模块负责**规则语义**（校验/分层/分流）。规则里以 ``/`` 开头的
锚定路径同样经 ``PathMapper.to_snapshot`` 归一，保证与存量排除口径一致。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.errors import ValidationError
from app.services.path_mapper import PathMapper

# restic --exclude-larger-than SIZE：整数/小数 + 可选单位（k/K/m/M/g/G/t/T），裸数字 = 字节
_SIZE_RE = re.compile(r"^\d+(?:\.\d+)?[kKmMgGtT]?$")


@dataclass(frozen=True)
class ExcludeRule:
    """规则列表一行：有序、可负向、可忽略大小写（negate 与 icase 互斥）。"""

    pattern: str
    negate: bool = False
    icase: bool = False


@dataclass(frozen=True)
class QuickExcludes:
    """快捷排除卡（零语法门槛，覆盖 90% 场景）。"""

    extensions: tuple[str, ...] = ()
    name_contains: str = ""
    larger_than: str | None = None
    if_present: tuple[str, ...] = ()
    caches: bool = False


@dataclass(frozen=True)
class ExcludeSpec:
    """任务的过滤条件（JSON 存列 `exclude_spec`；quick 展平 + rules 有序）。"""

    quick: QuickExcludes = field(default_factory=QuickExcludes)
    rules: tuple[ExcludeRule, ...] = ()


@dataclass(frozen=True)
class FlattenedExcludes:
    """展平后的 restic 参数集（供 ``WhitelistArgs`` 直接消费）。"""

    excludes: tuple[str, ...] = ()
    iexcludes: tuple[str, ...] = ()
    larger_than: str | None = None
    if_present: tuple[str, ...] = ()
    caches: bool = False


def validate_glob(pattern: str) -> str:
    """校验 restic（Go ``filepath.Match``）glob 语法，非法即 422。

    检查项：空值 / NUL / 未闭合或空字符类 / 花括号展开（restic 不展开且**静默空转**，
    属"看起来有、实际无效"的坑，按诚实原则直接拒绝）。

    Args:
        pattern: 排除模式（可含 ``*`` ``?`` ``[...]`` ``\\`` 转义与 ``/``）。

    Returns:
        原样返回（便于链式使用）。

    Raises:
        ValidationError: 语法非法（422 VALIDATION_ERROR）。
    """
    if not pattern or not pattern.strip():
        raise ValidationError("排除模式不能为空")
    if "\x00" in pattern:
        raise ValidationError("排除模式含非法字符", data={"pattern": pattern})
    if "{" in pattern or "}" in pattern:
        raise ValidationError(
            "不支持花括号展开（restic 会静默空转，不报错也不生效），请拆成多条规则",
            data={"pattern": pattern},
        )
    i, n = 0, len(pattern)
    while i < n:
        ch = pattern[i]
        if ch == "\\":
            i += 2  # 转义下一个字符
            continue
        if ch == "[":
            j = i + 1
            # 字符类否定标记（Go 用 ^；! 虽非 Go 语义也跳过，避免误判空类）
            if j < n and pattern[j] in ("^", "!"):
                j += 1
            start = j
            while j < n and pattern[j] != "]":
                j += 2 if pattern[j] == "\\" else 1
            if j >= n:
                raise ValidationError(
                    f"排除模式 {pattern!r} 的字符类未闭合（缺少 ]）", data={"pattern": pattern}
                )
            if j == start:
                raise ValidationError(
                    f"排除模式 {pattern!r} 的字符类为空", data={"pattern": pattern}
                )
            i = j + 1
            continue
        i += 1
    return pattern


def validate_size(size: str) -> str:
    """校验 ``--exclude-larger-than`` 取值（如 ``100M``），非法 422。"""
    value = (size or "").strip()
    if not value or not _SIZE_RE.match(value):
        raise ValidationError(
            f"文件大小阈值非法：{size!r}（数字 + 可选单位 k/m/g/t，如 100M）",
            data={"larger_than": size},
        )
    return value


def _normalize_rule_pattern(pattern: str, mapper: PathMapper) -> str:
    """规则 pattern 归一：以 ``/`` 开头的锚定路径 → 容器形态（与存量排除同口径）。"""
    if pattern.startswith("!"):
        raise ValidationError(
            "规则 pattern 不应自带 ! 前缀（负向请用 negate 开关）", data={"pattern": pattern}
        )
    if pattern.startswith("/"):
        return mapper.to_snapshot(pattern)
    return pattern


def spec_from_wire(raw: dict[str, Any]) -> ExcludeSpec:
    """前端/DB 的 camelCase dict → :class:`ExcludeSpec`（缺字段容错，非法条目跳过）。

    线上/落库形态与前端 TS 同形（``nameContains`` / ``largerThan`` / ``ifPresent``），
    后端内部统一转 snake_case 数据类——单一转换点，避免两套命名漂移。
    """
    quick_raw = raw.get("quick") or {}
    rules_raw = raw.get("rules") or []
    quick = QuickExcludes(
        extensions=tuple(str(x) for x in (quick_raw.get("extensions") or []) if str(x).strip()),
        name_contains=str(quick_raw.get("nameContains") or ""),
        larger_than=(str(quick_raw.get("largerThan")) if quick_raw.get("largerThan") else None),
        if_present=tuple(str(x) for x in (quick_raw.get("ifPresent") or []) if str(x).strip()),
        caches=bool(quick_raw.get("caches")),
    )
    rules = tuple(
        ExcludeRule(
            pattern=str(rule.get("pattern") or ""),
            negate=bool(rule.get("negate")),
            icase=bool(rule.get("icase")),
        )
        for rule in rules_raw
        if isinstance(rule, dict)
    )
    return ExcludeSpec(quick=quick, rules=rules)


def flatten_exclude_spec(spec: ExcludeSpec, mapper: PathMapper) -> FlattenedExcludes:
    """把 ExcludeSpec 展平为 restic 参数（校验 + 归一 + 分流）。

    Args:
        spec: 任务的过滤条件。
        mapper: 路径映射器（锚定路径归一用）。

    Returns:
        ``FlattenedExcludes``（excludes/iexcludes/larger_than/if_present/caches）。

    Raises:
        ValidationError: glob 语法非法、大小阈值非法、或 negate∧icase 组合（422）。
    """
    excludes: list[str] = []
    iexcludes: list[str] = []

    # ---- quick：生成锚定 pattern（顺序在前，便于后续负向取消） ----
    for ext in spec.quick.extensions:
        suffix = ext.strip()
        if not suffix:
            continue
        if not suffix.startswith("."):
            suffix = f".{suffix}"
        excludes.append(validate_glob(f"**/*{suffix}"))
    name = spec.quick.name_contains.strip()
    if name:
        excludes.append(validate_glob(f"**/*{name}*"))

    # ---- rules：按列表序，分流 --exclude / --iexclude ----
    for rule in spec.rules:
        raw = rule.pattern.strip()
        if not raw:
            continue
        if rule.negate and rule.icase:
            raise ValidationError(
                "负向规则不支持忽略大小写（--exclude 负向与 --iexclude 跨 flag 互不生效，"
                "实测见设计方案）",
                data={"pattern": rule.pattern},
            )
        pattern = validate_glob(_normalize_rule_pattern(raw, mapper))
        if rule.negate:
            excludes.append(f"!{pattern}")
        elif rule.icase:
            iexcludes.append(pattern)
        else:
            excludes.append(pattern)

    larger_than = validate_size(spec.quick.larger_than) if spec.quick.larger_than else None
    if_present = tuple(name.strip() for name in spec.quick.if_present if name.strip())

    return FlattenedExcludes(
        excludes=tuple(excludes),
        iexcludes=tuple(iexcludes),
        larger_than=larger_than,
        if_present=if_present,
        caches=bool(spec.quick.caches),
    )
