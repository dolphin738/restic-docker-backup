"""内置排除集（方案 §3.1 v2.3.2 模式感知）——D8 决策的唯一实现点。

语义核心集（与挂载模式无关的 8 条目）按 ``RESTIC_WEB_MODE`` 派生容器内前缀：
host → ``/host``、sources → ``/sources``；再叠加各模式的附加条目。
排除集为代码常量、不入库，UI 可追加不可删减（"不可删减"由本模块保证）。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Literal

from app.constants import SOURCE_ALLOWED_PREFIXES
from app.errors import PathOutOfPrefixError, ValidationError
from app.services.path_mapper import PathMapper
from app.settings import Settings, get_settings

logger = logging.getLogger(__name__)

Mode = Literal["host", "sources"]

# 用户排除规则的"锚定路径 vs 纯 glob 模式"分界：以 / 开头一律视为锚定路径（含 glob 通配）
_ANCHOR_PREFIX = "/"


class ExclusionRules:
    """内置排除集生成器（§7.3 ExclusionRules）。"""

    # 语义核心集：与挂载模式无关（方案 §3.1）
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

    HOST_PREFIX = "/host"
    SOURCES_PREFIX = "/sources"

    # host 模式附加：防套娃——部署目录下被再次扫到的 bind-mount 源（方案 §3.1）
    # repo：本地路径仓库 bind-mount 落在部署目录下时会被 /host 再次扫到（主理人裁决补入）
    HOST_EXTRA_DIRS: tuple[str, ...] = ("data", "cache", "restore", "repo")
    # 用于从挂载表反推部署目录的容器侧挂载点（先命中先使用）
    HOST_PROBE_MOUNTPOINTS: tuple[str, ...] = ("/data", "/cache/restic", "/cache", "/restore")

    # sources 模式附加：仓库/缓存特征路径（防止仓库数据被自己备份，命中时 UI 警告）
    SOURCES_EXTRA: tuple[str, ...] = ("repo", "restic-repo", "restic-cache", "cache")

    def __init__(self, settings: Settings | None = None) -> None:
        """构造排除集生成器。

        Args:
            settings: 运行期配置；缺省取进程单例。
        """
        self._settings = settings or get_settings()

    # ---------------------------------------------------------------- 公开接口（§7.3）

    def built_in(self, mode: Mode | None = None) -> list[str]:
        """返回指定模式下的内置排除规则（容器内路径前缀形式）。

        Args:
            mode: ``host`` 或 ``sources``；缺省取 ``settings.restic_web_mode``。

        Returns:
            按字符串去重且保持原始顺序的排除路径列表（避免同一路径重复出现在
            restic ``--exclude`` 参数中）。
        """
        resolved = mode or self._settings.restic_web_mode
        prefix = self.HOST_PREFIX if resolved == "host" else self.SOURCES_PREFIX

        rules = [f"{prefix}/{item}" for item in self.CORE]
        if resolved == "host":
            rules.extend(self._host_extra(prefix))
        else:
            rules.extend(f"{prefix}/{item}" for item in self.SOURCES_EXTRA)
        return _dedupe(rules)

    def is_builtin(self, rule: str, mode: Mode | None = None) -> bool:
        """判断某条排除规则是否属于内置集（供 UI 只读展示与"不可删减"校验）。

        Args:
            rule: 待判定的排除规则。
            mode: 模式；缺省取配置值。

        Returns:
            属于内置集返回 True。
        """
        if not rule:
            return False
        normalized = rule.rstrip("/") or "/"
        return normalized in set(self.built_in(mode))

    def core(self, mode: Mode | None = None) -> list[str]:
        """仅返回核心集派生结果（不含模式附加条目），供 UI 分组展示。"""
        resolved = mode or self._settings.restic_web_mode
        prefix = self.HOST_PREFIX if resolved == "host" else self.SOURCES_PREFIX
        return [f"{prefix}/{item}" for item in self.CORE]

    # ---------------------------------------------------------------- 内部实现

    def _host_extra(self, prefix: str) -> list[str]:
        """返回 host 模式附加条目（防套娃），解析失败时降级为空并记录告警。"""
        deploy_dir = self._resolve_deploy_dir()
        if not deploy_dir:
            logger.warning(
                "无法解析 compose 部署目录，host 模式防套娃排除条目未生成；"
                "可通过环境变量 RESTIC_WEB_DEPLOY_DIR 显式指定宿主机部署目录绝对路径"
            )
            return []
        return [f"{prefix}/{deploy_dir}/{name}" for name in self.HOST_EXTRA_DIRS]

    def _resolve_deploy_dir(self) -> str | None:
        """解析 compose 部署目录（宿主机绝对路径，去掉首尾 ``/``）。

        优先使用 ``RESTIC_WEB_DEPLOY_DIR``；未配置则从 ``/proc/self/mountinfo`` 反推
        （``/data`` 等 bind-mount 的挂载源即宿主机部署目录下的子目录）。
        """
        explicit = (self._settings.restic_web_deploy_dir or "").strip()
        if explicit:
            return explicit.strip("/")
        source = self._deploy_dir_from_mountinfo()
        if source:
            return source.strip("/")
        return None

    def _deploy_dir_from_mountinfo(self) -> str | None:
        """从 /proc/self/mountinfo 反推部署目录；不可用时返回 None。"""
        for mount_point in self.HOST_PROBE_MOUNTPOINTS:
            source = _mount_source(mount_point)
            if not source:
                continue
            parent = os.path.dirname(source)
            if os.path.basename(source.rstrip("/")) in self.HOST_EXTRA_DIRS and parent:
                return parent
            return source
        return None


def _mount_source(mount_point: str) -> str | None:
    """读取 /proc/self/mountinfo 中指定挂载点的挂载源（宿主机绝对路径）。

    Args:
        mount_point: 容器侧挂载点，如 ``/data``。

    Returns:
        挂载源路径；未找到或不可读时返回 None。
    """
    try:
        lines = Path("/proc/self/mountinfo").read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None

    for line in lines:
        fields = line.split()
        if len(fields) < 10 or fields[4] != mount_point:
            continue
        try:
            dash = fields.index("-", 6)
        except ValueError:
            continue
        if dash + 2 < len(fields):
            return fields[dash + 2]
    return None


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
    **容器路径**（``/host/etc/foo``）——不做边界归一，用户排除会在备份/dry-run 中
    **静默失效**。三分类规则：

    1. 以 ``/`` 开头 → 映射为容器形态后按 ``source_prefixes`` 校验：
       - 已带容器前缀（``/host`` / ``/sources``，含 glob 通配如 ``/etc/*.conf``）→
         视为已是容器形态，**不再二次叠加模式根**，按 ``source_prefixes`` 直接校验；
       - 其余 → :meth:`PathMapper.to_snapshot` 映射（``/etc/*.conf`` →
         ``/host/etc/*.conf``，restic 对 ``--exclude`` 的 glob 本就支持）；
    2. 不以 ``/`` 开头 → 原样透传（``*.tmp``、``node_modules`` 等纯模式，
       restic 无前导 ``/`` 的模式全树匹配，形态无关）；
    3. 校验越界（含**跨模式锚定**：host 模式下排除 ``/sources/...`` 实际指向容器内
       数据根，语义错误）→ 422 ``VALIDATION_ERROR``，**不静默保留**
       （静默保底正是本 bug 的成因）。

    Args:
        excludes: 用户追加的排除规则（展示形态、容器形态或纯 glob 模式）。
        mapper: 路径映射器。
        source_prefixes: 锚定路径允许落进的容器前缀白名单（**应传当前模式根**）。

    Returns:
        去重保序后的容器形态排除规则（供入库与 ``--exclude`` 下游直用）。

    Raises:
        ValidationError: 锚定路径（含跨模式前缀）越出 ``source_prefixes``（422）。
    """
    result: list[str] = []
    seen: set[str] = set()
    container_prefixes = (ExclusionRules.HOST_PREFIX, ExclusionRules.SOURCES_PREFIX)
    for raw in excludes:
        item = str(raw).strip()
        if not item or item in seen:
            continue
        seen.add(item)
        if not item.startswith(_ANCHOR_PREFIX):
            result.append(item)  # 纯 glob 模式：形态无关，原样透传
            continue
        # 已是容器形态：不二次叠加模式根，直接校验
        container = item if item.startswith(container_prefixes) else mapper.to_snapshot(item)
        try:
            container = mapper.validate(container, allowed_prefixes=source_prefixes)
        except PathOutOfPrefixError as exc:
            raise ValidationError(
                f"排除规则 {item!r} 不在当前模式根范围内", data={"exclude": item}
            ) from exc
        result.append(container)
    return result
