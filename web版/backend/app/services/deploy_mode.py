"""部署形态检测（架构设计文档 §5.1 部署形态检测；原方案《源数据过滤与树双模式设计方案》
已归档 2026-11-04，规则已上位化）。

一句话：**部署形态（Docker / 主机）自动检测，决定备份源树的浏览根**。

判定链：

    ``DEPLOY_MODE`` 显式指定 ``docker`` / ``host``  → 直接用（**覆盖一切自动判断**）
    ``auto``（默认，含非法值降级）                  → 自动检测
        ① ``/.dockerenv`` 存在                       → docker
        ② 兜底：``/proc/self/mountinfo`` 的根挂载点 fstype 以 ``overlay`` 开头 → docker
        ③ 其余                                        → host

边界情况（方案 §3.3）：

- **Windows 开发机**：``/.dockerenv`` 不存在、``/proc/self/mountinfo`` 不可读 → host；
- **WSL**：检测为 host，树里显示真实 ``/mnt/d/...``（利于调试）；
- **非 root 运行**：无权限目录走既有 403，树内显示"无权限"。

原则（方案 §3.1 加粗原文）：**自动检测只决定默认值，不充当安全边界**——
安全边界永远是 deny 清单 + 显式 env 覆盖。因此本模块**只读且不产生安全判定**，
deny 前缀的组装见 :func:`host_deny_prefixes`，是否放行由调用点（路由）决定。
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.constants import (
    DEFAULT_SOURCES_ROOT,
    DEPLOY_MODE_AUTO,
    DEPLOY_MODE_DOCKER,
    DEPLOY_MODE_HOST,
    DOCKER_ENV_FILE,
    HOST_BROWSE_ROOT,
    HOST_DENY_BASE_PREFIXES,
    MOUNTINFO_PATH,
    OVERLAY_FSTYPE_PREFIX,
)
from app.settings import Settings, get_settings

logger = logging.getLogger(__name__)

# 进程内缓存：key = (DEPLOY_MODE 原值, sources_root)，避免每个请求都读 mountinfo。
# 与 E30 的 REPO_STATUS_CACHE_SECONDS 不同，本结果是**进程级稳态事实**（部署形态不会
# 在运行中变化），故用永久缓存 + 显式失效函数（测试改 env 后调用），不做 TTL。
_cache: dict[tuple[str, str], str] = {}


def detect_deploy_mode(settings: Settings | None = None) -> str:
    """判定当前部署形态，返回 ``"docker"`` 或 ``"host"``。

    Args:
        settings: 运行期配置；缺省取进程单例。

    Returns:
        :data:`app.constants.DEPLOY_MODE_DOCKER` 或
        :data:`app.constants.DEPLOY_MODE_HOST`（**不会**返回 ``auto``）。
    """
    current = settings or get_settings()
    key = (str(current.deploy_mode or ""), str(current.sources_root or ""))
    cached = _cache.get(key)
    if cached is not None:
        return cached

    mode = _resolve(current)
    _cache[key] = mode
    logger.info(
        "部署形态检测：DEPLOY_MODE=%r → %s（浏览根 %s）",
        key[0] or DEPLOY_MODE_AUTO,
        mode,
        effective_browse_root(current),
    )
    return mode


def effective_browse_root(settings: Settings | None = None) -> str:
    """备份源树的**浏览根**（设计方案 §3.2 的单一事实源）。

    ``docker`` → ``settings.sources_root``（默认 ``/sources``，容器内的可备份面）；
    ``host``   → ``/``（真实文件系统，主机部署下这才是可备份数据）。

    ``PathMapper``、源路径校验、排除规则前缀映射**全部改读本值**——
    UI 允许选的路径与后端接受写入的路径因此永不不一致（S2 argv 白名单的前置条件）。

    Args:
        settings: 运行期配置；缺省取进程单例。

    Returns:
        规范化的绝对根路径（无尾斜杠，根仍为 ``/``）。
    """
    current = settings or get_settings()
    if detect_deploy_mode(current) == DEPLOY_MODE_HOST:
        return HOST_BROWSE_ROOT
    root = (current.sources_root or "").strip().rstrip("/")
    return root or DEFAULT_SOURCES_ROOT


def host_deny_prefixes(settings: Settings | None = None) -> tuple[str, ...]:
    """host 模式下浏览与快跳的 deny 前缀（设计方案 §3.3）。

    组成 = 系统伪目录基底 + 运行时路径：

    - ``repo_root``（仓库自身存储位置）——否则备份任务会把仓库数据卷进自己的快照，
      形成**递归膨胀**；
    - ``passfile_root`` / ``data_dir``（应用数据目录，含 SQLite DB 与仓库密码文件）——
      E33 的 :func:`app.routers.fs._reject_passfile_root` 已挡密码文件目录，
      但**应用 DB 目录此前无挡位**，故在此补齐。

    Args:
        settings: 运行期配置；缺省取进程单例。

    Returns:
        去重保序的前缀元组（均为无尾斜杠的绝对路径）。
    """
    current = settings or get_settings()
    items: list[str] = list(HOST_DENY_BASE_PREFIXES)
    for raw in (current.repo_root, current.passfile_root, current.data_dir):
        item = (raw or "").strip().rstrip("/")
        if item:
            items.append(item)
    return _dedupe(items)


def invalidate_deploy_mode_cache() -> None:
    """清空检测缓存（测试改 ``DEPLOY_MODE`` / ``SOURCES_ROOT`` 后调用）。"""
    _cache.clear()


def _resolve(settings: Settings) -> str:
    """按「显式值优先 → 自动检测」解析，非法显式值降级为 auto 并留痕。"""
    raw = (settings.deploy_mode or "").strip().lower()
    if raw in (DEPLOY_MODE_DOCKER, DEPLOY_MODE_HOST):
        return raw
    if raw and raw != DEPLOY_MODE_AUTO:
        logger.warning(
            "DEPLOY_MODE=%r 非法（允许 %s），回落自动检测",
            raw,
            "/".join((DEPLOY_MODE_AUTO, DEPLOY_MODE_DOCKER, DEPLOY_MODE_HOST)),
        )
    return _auto_detect()


def _auto_detect() -> str:
    """自动检测：``/.dockerenv`` → 根分区 overlay 兜底。"""
    if Path(DOCKER_ENV_FILE).exists():
        return DEPLOY_MODE_DOCKER
    return DEPLOY_MODE_DOCKER if _root_is_overlay() else DEPLOY_MODE_HOST


def _root_is_overlay() -> bool:
    """``/proc/self/mountinfo`` 中根挂载点的 fstype 是否为 overlay 系。

    mountinfo 字段（内核文档 ``proc.txt``）：
        ``36 35 98:0 /mnt1 /mnt2 rw,noatime master:1 - ext3 /dev/root rw,errors=continue``
    索引 4 = 挂载点，索引 8 = 文件系统类型（在 ``-`` 分隔符之后）。
    不可读（Windows / 受限容器）时返回 False（→ host）。
    """
    try:
        text = Path(MOUNTINFO_PATH).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    for line in text.splitlines():
        fields = line.split()
        if len(fields) >= 9 and fields[4] == "/":
            return fields[8].startswith(OVERLAY_FSTYPE_PREFIX)
    return False


def _dedupe(items: list[str]) -> tuple[str, ...]:
    """去重并保持原始顺序。"""
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return tuple(result)
