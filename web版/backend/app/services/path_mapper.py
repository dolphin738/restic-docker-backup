"""PathMapper（§7.3，R-005）：`/host`|`/sources` 前缀 ↔ 原路径语义的唯一实现点。

UI 全程展示**剥掉前缀**的原路径（`/etc/nginx`），存储层恒带容器前缀（`/host/etc/nginx`）；
所有进入 restic 命令或文件系统访问的容器路径必须先过 :meth:`PathMapper.validate`。
"""

from __future__ import annotations

import posixpath

from app.errors import PathOutOfPrefixError
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
        """当前模式的备份源根前缀：host 模式 ``/host``，sources 模式 ``/sources``。"""
        return self._settings.mode_root

    def to_snapshot(self, display_path: str) -> str:
        """展示路径 → 快照内存储路径（``/etc/nginx`` → ``/host/etc/nginx``）。

        Args:
            display_path: UI 侧原路径语义。

        Returns:
            带容器前缀的规范化路径。
        """
        normalized = self._normalize(display_path)
        prefix = self.PREFIX
        if normalized == prefix or normalized.startswith(prefix + "/"):
            return normalized
        if normalized == "/":
            return prefix
        return posixpath.join(prefix, normalized.lstrip("/"))

    def to_display(self, stored_path: str) -> str:
        """快照内存储路径 → 展示路径（``/host/etc/nginx`` → ``/etc/nginx``）。

        Args:
            stored_path: 带容器前缀的路径。

        Returns:
            剥掉前缀后的原路径语义；不带前缀时原样返回（已是展示形态）。
        """
        if not stored_path:
            return ""
        normalized = self._normalize(stored_path)
        for prefix in (self._settings.host_root, self._settings.sources_root):
            if normalized == prefix:
                return "/"
            if normalized.startswith(prefix + "/"):
                return normalized[len(prefix):] or "/"
        return normalized

    def validate(
        self,
        path: str,
        *,
        allowed_prefixes: tuple[str, ...] = ("/host", "/sources", "/restore"),
    ) -> str:
        """规范化路径并校验其落在白名单前缀内（方案 §6 注入防护）。

        Args:
            path: 待校验路径（容器内绝对路径）。
            allowed_prefixes: 允许的前缀集合。

        Returns:
            规范化后的安全路径。

        Raises:
            PathValidationError: 路径为空、含非法字符或越出白名单前缀（400）。
        """
        normalized = self._normalize(path)
        for prefix in allowed_prefixes:
            if normalized == prefix or normalized.startswith(prefix + "/"):
                return normalized
        raise PathValidationError(
            f"路径 {path!r} 不在允许的前缀范围内（{', '.join(allowed_prefixes)}）",
            data={"path": path, "allowed_prefixes": list(allowed_prefixes)},
        )

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
        # normpath 会把 /host/../etc 折叠为 /etc，随后由前缀校验拦下（防目录穿越）
        normalized = posixpath.normpath(candidate)
        if not normalized.startswith("/"):
            raise PathValidationError(f"路径 {path!r} 无法解析为绝对路径")
        return normalized
