"""内置排除集（A0 模式收敛后唯一形态：备份源根恒为 ``/sources``）——D8 决策的唯一实现点。

语义核心集（8 条目）统一派生 ``/sources`` 容器前缀，再叠加 sources 形态附加条目
（仓库/缓存特征路径，防止仓库数据被自己备份，命中时 UI 警告）。
排除集为代码常量、不入库，UI 可追加不可删减（"不可删减"由本模块保证）。
"""

from __future__ import annotations

from app.constants import SOURCE_ALLOWED_PREFIXES
from app.errors import PathOutOfPrefixError, ValidationError
from app.services.path_mapper import PathMapper
from app.settings import Settings, get_settings

# 用户排除规则的"锚定路径 vs 纯 glob 模式"分界：以 / 开头一律视为锚定路径（含 glob 通配）
_ANCHOR_PREFIX = "/"


class ExclusionRules:
    """内置排除集生成器（§7.3）。"""

    # 语义核心集：与挂载形态无关（方案 §3.1）
    CORE: tuple[str, ...] = (
        "proc",
        "sys",
        "dev",
        "run",
        "var/run",
        "var/lib/docker",
        "tmp",
        "lost+found",
    )

    SOURCES_PREFIX = "/sources"

    # sources 形态附加：仓库/缓存特征路径（防止仓库数据被自己备份，命中时 UI 警告）
    SOURCES_EXTRA: tuple[str, ...] = ("repo", "restic-repo", "restic-cache", "cache")

    def __init__(self, settings: Settings | None = None) -> None:
        """构造排除集生成器。

        Args:
            settings: 运行期配置；缺省取进程单例。
        """
        self._settings = settings or get_settings()

    # ---------------------------------------------------------------- 公开接口（§7.3）

    def built_in(self) -> list[str]:
        """返回内置排除规则（容器内路径前缀形式）。

        Returns:
            按字符串去重且保持原始顺序的排除路径列表（避免同一路径重复出现在
            restic ``--exclude`` 参数中）。
        """
        prefix = self.SOURCES_PREFIX
        rules = [f"{prefix}/{item}" for item in self.CORE]
        rules.extend(f"{prefix}/{item}" for item in self.SOURCES_EXTRA)
        return _dedupe(rules)

    def is_builtin(self, rule: str) -> bool:
        """判断某条排除规则是否属于内置集（供 UI 只读展示与"不可删减"校验）。

        Args:
            rule: 待判定的排除规则。

        Returns:
            属于内置集返回 True。
        """
        if not rule:
            return False
        normalized = rule.rstrip("/") or "/"
        return normalized in set(self.built_in())

    def core(self) -> list[str]:
        """仅返回核心集派生结果（不含附加条目），供 UI 分组展示。"""
        prefix = self.SOURCES_PREFIX
        return [f"{prefix}/{item}" for item in self.CORE]


def _dedupe(rules: list[str]) -> list[str]:
    """去重并保持原始顺序。"""
    seen: set[str] = set()
    result: list[str] = []
    for rule in rules:
        if rule in seen:
            continue
        seen.add(rule)
        result.append(rule)
    return result


def normalize_user_excludes(
    excludes: list[str],
    mapper: PathMapper,
    *,
    source_prefixes: tuple[str, ...] = SOURCE_ALLOWED_PREFIXES,
) -> list[str]:
    """用户追加排除规则的**三分类归一**（形态换算的单一实现点，主理人裁决定稿）。

    背景：UI 只能收**展示形态**（``/etc/foo``），而 restic ``--exclude`` 匹配的是
    **容器路径**（``/sources/etc/foo``）——不做边界归一，用户排除会在备份/dry-run 中
    **静默失效**。三分类规则：

    1. 以 ``/`` 开头 → 映射为容器形态后按 ``source_prefixes`` 校验：
       - 已带容器前缀（``/sources``，含 glob 通配如 ``/etc/*.conf``）→
         视为已是容器形态，**不再二次叠加源根**，按 ``source_prefixes`` 直接校验；
       - 其余 → :meth:`PathMapper.to_snapshot` 映射（``/etc/*.conf`` →
         ``/sources/etc/*.conf``，restic 对 ``--exclude`` 的 glob 本就支持）；
    2. 不以 ``/`` 开头 → 原样透传（``*.tmp``、``node_modules`` 等纯模式，
       restic 无前导 ``/`` 的模式全树匹配，形态无关）；
    3. 校验越界（含跨形态锚定：排除 ``/host/...`` 已无对应挂载，语义错误）→
       422 ``VALIDATION_ERROR``，**不静默保留**（静默保底正是本 bug 的成因）。

    Args:
        excludes: 用户追加的排除规则（展示形态、容器形态或纯 glob 模式）。
        mapper: 路径映射器。
        source_prefixes: 锚定路径允许落进的容器前缀白名单（**应传当前源根**）。

    Returns:
        去重保序后的容器形态排除规则（供入库与 ``--exclude`` 下游直用）。

    Raises:
        ValidationError: 锚定路径（含跨形态前缀）越出 ``source_prefixes``（422）。
    """
    result: list[str] = []
    seen: set[str] = set()
    container_prefixes = (ExclusionRules.SOURCES_PREFIX,)
    for raw in excludes:
        item = str(raw).strip()
        if not item or item in seen:
            continue
        seen.add(item)
        if not item.startswith(_ANCHOR_PREFIX):
            result.append(item)  # 纯 glob 模式：形态无关，原样透传
            continue
        # 已是容器形态：不二次叠加源根，直接校验
        container = item if item.startswith(container_prefixes) else mapper.to_snapshot(item)
        try:
            container = mapper.validate(container, allowed_prefixes=source_prefixes)
        except PathOutOfPrefixError as exc:
            raise ValidationError(
                f"排除规则 {item!r} 不在当前源根范围内", data={"exclude": item}
            ) from exc
        result.append(container)
    return result
