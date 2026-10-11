"""E33 容器文件系统浏览：仓库配置页「浏览路径」的数据源（v1.0.x 新增端点）。

与 E05（备份源浏览）的差异：

- **无白名单前缀**：E05 限定 ``/host`` / ``/sources``（可备份面），而仓库地址可以落在
  容器内任意已挂载位置（``/repo``、用户自挂的 ``/mnt/...``），故本端点允许浏览**整个
  容器文件系统**（只读列目录名，不回文件内容）。鉴权同其余业务端点（会话 + 首启完成），
  单管理员产品形态下风险可控；``/proc/mounts`` 只用于**标注挂载点**，不回挂载配置。
- **无展示形态换算**：E05 回剥前缀的 display_path，本端点直接回容器绝对路径
  （repo.url 本身就是容器形态，``PathMapper`` 的模式根换算在这里不适用）。
- 复用 :mod:`app.services.dir_scan` 的单层扫描与游标分页（D8 单一事实来源）。
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path, PurePosixPath

from fastapi import APIRouter, Depends, Query, Request

from app.auth.dependencies import require_bootstrap_done, require_session
from app.auth.session import SessionContext
from app.constants import SOURCE_BROWSE_DEFAULT_LIMIT, SOURCE_BROWSE_MAX_LIMIT
from app.errors import ValidationError
from app.schemas.api_schemas import FsBrowseResponse, FsItem
from app.services.dir_scan import cursor_index, encode_cursor, scan_dir_async

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["fs"])

_PROTECTED = [Depends(require_session), Depends(require_bootstrap_done)]

# /proc/mounts 里的伪文件系统：不是用户可用的挂载位置，快跳列表里没有意义
_PSEUDO_FS_TYPES = frozenset(
    {
        "proc", "sysfs", "devtmpfs", "devpts", "tmpfs", "cgroup", "cgroup2", "mqueue",
        "shm", "nsfs", "bpf", "tracefs", "debugfs", "securityfs", "pstore", "configfs",
        "fusectl", "autofs", "hugetlbfs", "overlay", "squashfs",
    }
)
# 挂载点前缀黑名单：系统伪目录（/run/secrets 等敏感位置也不做快跳入口）
_MOUNT_PREFIX_DENY = ("/proc", "/sys", "/dev", "/run")


@router.get(
    "/fs/browse",
    response_model=FsBrowseResponse,
    summary="E33 容器文件系统浏览（仓库路径选择）",
    dependencies=_PROTECTED,
)
async def browse_fs(
    request: Request,
    path: str = Query(default="/", description="容器内绝对路径；缺省为根目录"),
    cursor: str | None = Query(default=None, description="上一页末条目的不透明游标"),
    limit: int = Query(default=SOURCE_BROWSE_DEFAULT_LIMIT, ge=1, le=SOURCE_BROWSE_MAX_LIMIT),
    session: SessionContext = Depends(require_session),
) -> FsBrowseResponse:
    """单层浏览容器文件系统，标注挂载点；仅根目录请求回 ``mounts`` 快跳列表。

    错误：422 VALIDATION_ERROR（相对路径）/ 404（不存在）/ 422（不是目录）/ 403（无权限）。
    """
    del session
    container = _normalize(path)
    entries = await scan_dir_async(container)
    mounts = await asyncio.to_thread(_mount_points)

    start_index = cursor_index(entries, cursor)
    window = entries[start_index : start_index + limit]
    base = container.rstrip("/")
    items = [
        FsItem(
            name=name,
            type=kind,
            path=f"{base}/{name}" if base else f"/{name}",
            is_mountpoint=f"{base}/{name}" in mounts,
        )
        for name, kind in window
    ]
    has_more = start_index + limit < len(entries)
    next_cursor = encode_cursor(window[-1][0]) if (has_more and window) else None
    return FsBrowseResponse(
        path=container,
        items=items,
        mounts=sorted(mounts) if container == "/" else [],
        next_cursor=next_cursor,
    )


def _normalize(raw: str) -> str:
    """规范化为容器内绝对路径：折叠 ``..``/``.`` 与重复斜杠；拒绝相对路径。

    全文件系统浏览是本端点的既定语义（见模块 docstring），``..`` 经 normpath
    折叠后仍是绝对路径即可放行，无需前缀校验。
    """
    cleaned = PurePosixPath(raw or "/").as_posix()
    if not cleaned.startswith("/"):
        # PurePosixPath('etc') → 'etc'：相对路径直接拒绝，避免相对进程 cwd 的歧义
        raise ValidationError(f"路径必须是容器内绝对路径：{raw}")
    # 折叠 .. 与重复斜杠（/a/../b → /b）
    parts: list[str] = []
    for part in cleaned.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    return "/" + "/".join(parts) if parts else "/"


def _mount_points() -> set[str]:
    """读取 ``/proc/mounts`` 得到容器挂载点集合；不可读（如 Windows 开发机）返回空集。

    过滤：伪文件系统类型、系统伪目录前缀、根目录本身；八进制转义（\\040 空格）还原。
    """
    try:
        text = Path("/proc/mounts").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return set()
    mounts: set[str] = set()
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 3:
            continue
        _source, target, fstype = fields[0], fields[1], fields[2]
        target = target.replace("\\040", " ").replace("\\011", "\t")
        if fstype in _PSEUDO_FS_TYPES or target == "/":
            continue
        if target.startswith(_MOUNT_PREFIX_DENY):
            continue
        mounts.add(target)
    return mounts
