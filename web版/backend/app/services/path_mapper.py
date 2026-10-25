"""PathMapper（§7.3，R-005）：`/sources` 前缀 ↔ 原路径语义的唯一实现点（A0 收敛后唯一形态）。

UI 全程展示**剥掉前缀**的原路径（`/etc/nginx`），存储层恒带容器前缀（`/sources/etc/nginx`）；
所有进入 restic 命令或文件系统访问的容器路径必须先过 :meth:`PathMapper.validate`。
"""

from __future__ import annotations

import posixpath

from app.errors import PathOutOfPrefixError
from app.services.deploy_mode import host_deny_prefixes
from app.settings import Settings, get_settings


class PathValidationError(PathOutOfPrefixError):
    """PathMapper 抛出的路径越界错误（§7.3 命名），语义等价 400 ``PATH_OUT_OF_PREFIX``。"""


class PathMapper:
    """容器路径 ↔ 展示路径的双向映射 + 白名单校验。"""

    def __init__(self, settings: Settings | None = None) -> None:
        """构造映射器。

        Args:
            settings: 运行期配置；缺省取进程单例（决定了当前模式前缀）。
        """
        self._settings = settings or get_settings()

    @property
    def PREFIX(self) -> str:  # noqa: N802 - 与 §7.3 签名保持一致
        """备份源根前缀（由部署形态派生，见 :func:`source_allowed_prefixes`）。"""
        return source_allowed_prefixes(self._settings)[0]

    def to_snapshot(self, display_path: str) -> str:
        """展示路径 → 快照内存储路径（``/etc/nginx`` → ``/sources/etc/nginx``）。

        Args:
            display_path: UI 侧原路径语义。

        Returns:
            带容器前缀的规范化路径。
        """
        normalized = self._normalize(display_path)
        prefix = self.PREFIX
        if prefix == "/":
            # host 模式：浏览根即 /，展示形态与容器形态同一（无前缀可叠加）
            return normalized
        if normalized == prefix or normalized.startswith(prefix + "/"):
            return normalized
        if normalized == "/":
            return prefix
        return posixpath.join(prefix, normalized.lstrip("/"))

    def to_display(self, stored_path: str) -> str:
        """快照内存储路径 → 展示路径（``/sources/etc/nginx`` → ``/etc/nginx``）。

        Args:
            stored_path: 带容器前缀的路径。

        Returns:
            剥掉前缀后的原路径语义；不带前缀时原样返回（已是展示形态）。
        """
        if not stored_path:
            return ""
        normalized = self._normalize(stored_path)
        prefix = self._settings.browse_root
        if prefix == "/":
            # host 模式：无前缀可剥，存储形态即展示形态
            return normalized
        if normalized == prefix:
            return "/"
        if normalized.startswith(prefix + "/"):
            return normalized[len(prefix):] or "/"
        return normalized

    def validate(
        self,
        path: str,
        *,
        allowed_prefixes: tuple[str, ...] | None = None,
        deny_prefixes: tuple[str, ...] | None = None,
    ) -> str:
        """规范化路径并校验其落在白名单前缀内（方案 §6 注入防护）+ deny 边界。

        Args:
            path: 待校验路径（容器内绝对路径）。
            allowed_prefixes: 允许的前缀集合；缺省为「备份源根 + 恢复根」——
                两者**均派生于当前部署形态**（设计方案 §3.2），不写死 ``/sources``，
                否则 host 模式下 UI 可选路径与后端接受写入的路径会不一致。
            deny_prefixes: 拒绝前缀集合（仅对**备份源根**下的路径生效）；缺省取
                :func:`app.services.deploy_mode.host_deny_prefixes`。host 模式用于禁止
                把仓库自身存储位置 / 应用数据目录选作备份源（防递归备份、防泄露）。

        Returns:
            规范化后的安全路径。

        Raises:
            PathValidationError: 路径为空、含非法字符、越出白名单前缀，或（源根下）
                落在 deny 区域内（400）。
        """
        normalized = self._normalize(path)
        prefixes = (
            allowed_prefixes
            if allowed_prefixes is not None
            else (self._settings.browse_root, self._settings.restore_root)
        )
        matched: str | None = None
        for prefix in prefixes:
            if self._is_under(normalized, prefix):
                matched = prefix
                break
        if matched is None:
            raise PathValidationError(
                f"路径 {path!r} 不在允许的前缀范围内（{', '.join(prefixes)}）",
                data={"path": path, "allowed_prefixes": list(prefixes)},
            )
        if matched == self._settings.browse_root:
            # deny 边界（设计方案 §3.3）：host 模式浏览根为 "/"，安全边界由 deny 承担——
            # 仓库自身存储位置（防递归备份）与应用数据目录（DB / 密码文件）不可作为备份源。
            # 仅对**备份源根**生效：恢复目标（/restore）与快照内路径不受影响。
            denies = (
                deny_prefixes
                if deny_prefixes is not None
                else host_deny_prefixes(self._settings)
            )
            for deny in denies:
                if self._is_under(normalized, deny):
                    raise PathValidationError(
                        f"路径 {path!r} 落在受保护区域内（{deny}）："
                        "仓库存储位置与应用数据目录不可作为备份源或恢复目标",
                        data={"path": path, "denied_prefix": deny},
                    )
        return normalized

    @staticmethod
    def _is_under(normalized: str, prefix: str) -> bool:
        """判断规范化绝对路径是否落在 ``prefix`` 之内。

        ``prefix == "/"``（host 模式浏览根）不能用 ``startswith("//")`` 判定——任何正常
        路径都不匹配 ``//``——改为「是绝对路径即命中」，覆盖全部绝对路径。
        """
        if prefix == "/":
            return normalized.startswith("/")
        return normalized == prefix or normalized.startswith(prefix + "/")

    @staticmethod
    def _normalize(path: str) -> str:
        """POSIX 规范化：折叠 ``..``、``.`` 与重复斜杠，并拒绝空值与 NUL。

        Args:
            path: 原始路径。

        Returns:
            规范化绝对路径。

        Raises:
            PathValidationError: 路径为空、含 NUL 或相对路径无法归一化。
        """
        if not path or not path.strip():
            raise PathValidationError("路径不能为空")
        if "\x00" in path:
            raise PathValidationError("路径含非法字符")
        candidate = path.strip()
        if not candidate.startswith("/"):
            candidate = "/" + candidate
        # normpath 会把 /sources/../etc 折叠为 /etc，随后由前缀校验拦下（防目录穿越）
        normalized = posixpath.normpath(candidate)
        if not normalized.startswith("/"):
            raise PathValidationError(f"路径 {path!r} 无法解析为绝对路径")
        return normalized


def source_allowed_prefixes(settings: Settings | None = None) -> tuple[str, ...]:
    """备份源**生效白名单**（设计方案 §3.2：由 browse_root 单一派生）。

    取代原先硬编码的 ``SOURCE_ALLOWED_PREFIXES`` 常量——host 模式下浏览根是 ``/``，
    可备份面随之放开，安全边界由 deny 前缀承担（方案 §3.1 原则）。

    Args:
        settings: 运行期配置；缺省取进程单例。

    Returns:
        单元素前缀元组（``("/sources",)`` 或 ``("/",)``）。
    """
    return ((settings or get_settings()).browse_root,)


def restore_allowed_prefixes(settings: Settings | None = None) -> tuple[str, ...]:
    """恢复目标**生效白名单**（时间戳子目录流，E19/E20 共用；owner 2026-10-25 裁决 Q1=A）。

    与备份源同源的派生纪律（方案 §3.2「UI 可选路径与后端接受写入的路径同源」）：

    - docker：``(settings.restore_root,)``（= ``/restore``，专用可写挂载点）；
    - host：``(settings.browse_root,)``（= ``/``，真实文件系统）——此时快照里存的就是
      主机绝对路径，恢复到主机任意可写目录才有意义；安全边界由 deny 前缀承担
      （``PathMapper.validate`` 对 browse_root 命中时强制检查 ``host_deny_prefixes``）。

    Args:
        settings: 运行期配置；缺省取进程单例。

    Returns:
        单元素前缀元组（docker ``("/restore",)`` / host ``("/",)``）。
    """
    resolved = settings or get_settings()
    if resolved.browse_root == "/":
        return (resolved.browse_root,)
    return (resolved.restore_root,)


def inplace_restore_allowed_prefixes(settings: Settings | None = None) -> tuple[str, ...]:
    """原位恢复目标**生效白名单**：写回的是快照内路径 → 与备份源白名单同源。

    docker → ``("/sources",)``；host → ``("/",)``（owner 2026-10-25 裁决 Q1=A）。
    历史注释「与部署形态无关，故保持固定值」已被取证推翻：host 模式快照存的是主机
    绝对路径（如 ``/srv/data``），固定 ``/sources`` 会让合法原位恢复直接 400。

    Args:
        settings: 运行期配置；缺省取进程单例。

    Returns:
        单元素前缀元组（与 :func:`source_allowed_prefixes` 同值）。
    """
    return source_allowed_prefixes(settings)


def browse_allowed_prefixes(settings: Settings | None = None) -> tuple[str, ...]:
    """E05/E33 浏览允许的前缀：与备份源同源（见 :func:`source_allowed_prefixes`）。"""
    return source_allowed_prefixes(settings)
