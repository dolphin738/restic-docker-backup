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
from app.errors import ForbiddenError, InternalError, ValidationError
from app.schemas.api_schemas import (
    FsBrowseResponse,
    FsItem,
    FsMkdirRequest,
    FsMkdirResponse,
)
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
# 挂载点前缀黑名单：系统伪目录 + 应用数据卷
# /data 自 E5（2026-10-16）起存放仓库密码文件（/data/repos/<repo_id>/password），
# 虽只列目录名不回内容，仍不为其提供快跳入口（owner 裁决）。
_MOUNT_PREFIX_DENY = ("/proc", "/sys", "/dev", "/run", "/data")

# 创建目录（写操作）额外挡掉的 FHS 系统目录：E33 浏览是**只读**（看一眼无副作用），
# mkdir 是**写**，故口径比浏览严一档——仓库永远不该落在这些位置，挡掉不影响
# 合法用途（仓库默认在 ``settings.repo_root``，用户自挂卷在 ``/mnt/...``）。
_WRITE_PREFIX_DENY = (
    "/proc", "/sys", "/dev", "/run", "/etc", "/bin", "/sbin", "/usr", "/lib", "/lib64",
    "/boot", "/var", "/root",
)
# 单级目录名长度上限（ext4/overlayfs/XFS 通用 255 字节；按 UTF-8 字节计，中文名 3 字节/字）
_MAX_NAME_BYTES = 255


def _reject_write_root(container: str) -> None:
    """拒绝在**系统目录**内创建目录（写操作专用口径，比浏览严一档）。

    Args:
        container: 已规范化的容器内绝对路径。

    Raises:
        ValidationError: 路径落在系统目录内（422；不做部分放行，避免口径碎片）。
    """
    for prefix in _WRITE_PREFIX_DENY:
        if container == prefix or container.startswith(prefix + "/"):
            raise ValidationError(
                f"不能在系统目录内创建文件夹：{container}",
                data={"path": container, "denied_root": prefix},
            )


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

    错误：422 VALIDATION_ERROR（相对路径 / **落在密码文件目录内**，见
    :func:`_reject_passfile_root`）/ 404（不存在）/ 422（不是目录）/ 403（无权限）。
    """
    del session
    container = _normalize(path)
    _reject_app_data_root(container, request.app.state.settings.data_dir)
    _reject_passfile_root(container, request.app.state.settings.passfile_root)
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


@router.post(
    "/fs/mkdir",
    response_model=FsMkdirResponse,
    summary="E33 创建目录（仓库路径选择：容器视角单级创建）",
    dependencies=_PROTECTED,
)
async def mkdir_fs(
    payload: FsMkdirRequest,
    request: Request,
    session: SessionContext = Depends(require_session),
) -> FsMkdirResponse:
    """创建容器视角的单级目录（供仓库/crypt 本地目录的目录树直接建文件夹）。

    **单级**语义（不做 ``mkdir -p``）：父目录必须已存在，缺父级即报错——递归创建
    会让手输错路径的用户在不知情的情况下建出一串目录树，事后难以清理。

    口径比 :func:`browse_fs` **严一档**：浏览只读（看一眼无副作用），本端点是写，
    故在浏览的两条拒绝（应用数据目录 / 密码文件目录）之外，再挡系统目录前缀与根。
    这三组拒绝都跟随 settings（唯一出处），不在本文件散落路径字面量。

    已存在**目录** → 幂等返回 ``created=false``（不报错，前端据此提示「已存在」）；
    同名**文件**存在 → 422（不能拿文件当目录）。

    错误：422（相对路径 / 根目录 / 系统目录 / 应用数据目录 / 密码目录 / 名称过长 /
    父目录不存在 / 同名文件已存在）/ 403（无权限）/ 500（其他 OSError）。
    """
    del session
    container = _normalize(payload.path)
    if container == "/":
        raise ValidationError("不能在文件系统根目录上创建文件夹", data={"path": container})
    _reject_write_root(container)
    _reject_app_data_root(container, request.app.state.settings.data_dir)
    _reject_passfile_root(container, request.app.state.settings.passfile_root)
    name = container.rsplit("/", 1)[-1]
    if not name or len(name.encode("utf-8")) > _MAX_NAME_BYTES:
        raise ValidationError(
            f"文件夹名称不可为空且不得超过 {_MAX_NAME_BYTES} 字节：{name}",
            data={"path": container, "name": name},
        )
    created = await asyncio.to_thread(_mkdir_sync, container)
    return FsMkdirResponse(path=container, created=created)


def _mkdir_sync(container: str) -> bool:
    """同步创建单级目录（经 ``asyncio.to_thread`` 调用，不阻塞事件循环）。

    Returns:
        True = 本次真的创建了；False = 目标已是目录（幂等）。

    Raises:
        ValidationError: 父目录不存在 / 同名文件已存在。
        ForbiddenError: 无权限。
        InternalError: 其他 OSError（名称非法、只读文件系统等）。
    """
    parent = str(PurePosixPath(container).parent) or "/"
    if not Path(parent).is_dir():
        raise ValidationError(
            f"父目录不存在，无法创建：{parent}",
            data={"path": container, "parent": parent},
        )
    target = Path(container)
    if target.exists():
        if target.is_dir():
            return False
        raise ValidationError(
            f"同名文件已存在，无法创建文件夹：{container}", data={"path": container}
        )
    try:
        target.mkdir(mode=0o755)
    except FileExistsError:
        # 并发创建同一路径：谁先到谁说话，后到的视为「已存在」
        return False
    except PermissionError as exc:
        raise ForbiddenError(f"无权限创建文件夹：{container}", data={"path": container}) from exc
    except OSError as exc:
        raise InternalError(
            f"创建文件夹失败：{container}（{exc.strerror or exc}）", data={"path": container}
        ) from exc
    return True


def _reject_app_data_root(container: str, data_dir: str | Path) -> None:
    """拒绝浏览**应用数据目录**及其子目录（批次③ owner 裁决 B，2026-10-24）。

    动机：E33 是全文件系统浏览（仓库选径的既定语义），host 模式下会把应用数据目录
    （SQLite 库、运行日志、任务日志、仓库密码文件）的**目录名**暴露在仓库路径选择器里。
    方案 B 只挡数据目录这一棵、**保留 ``/repo``**——仓库默认就在 ``settings.repo_root``，
    把它一起挡掉会让仓库选不到默认位置（功能自相矛盾）。

    与 :func:`_reject_passfile_root` 的分工：后者管「密码文件目录」这棵更细的子树
    （可独立于 data_dir 配置），本函数管整棵数据目录；两者叠加不冲突（细的先报）。

    判定跟随 ``settings.data_dir``（唯一出处，env ``DATA_DIR`` 可覆盖），不在本文件
    硬编码路径前缀——改了根目录也不会漏挡。

    Args:
        container: 已规范化的容器内绝对路径。
        data_dir: 应用数据目录根（``settings.data_dir``）。

    Raises:
        ValidationError: 路径落在该根目录内（422；不回任何目录条目）。
    """
    root = str(data_dir or "").strip().rstrip("/")
    if not root:
        return
    if container == root or container.startswith(root + "/"):
        raise ValidationError(
            f"该路径位于应用数据目录内，不可浏览或选作仓库路径：{container}",
            data={"path": container, "denied_root": root},
        )


def _reject_passfile_root(container: str, passfile_root: str) -> None:
    """拒绝浏览**仓库密码文件目录**及其子目录（E5，owner 2026-10-16 裁决 A）。

    全文件系统浏览是本端点的既定语义（见模块 docstring），故只在**密码文件根目录这一棵
    子树**上开洞——判定跟随 ``settings.passfile_root``（唯一出处，env ``PASSFILE_ROOT``
    可覆盖），不在本文件硬编码路径前缀；这样改了根目录也不会漏挡。

    Args:
        container: 已规范化的容器内绝对路径。
        passfile_root: 密码文件根目录（``settings.passfile_root``）。

    Raises:
        ValidationError: 路径落在该根目录内（422；不回任何目录条目）。
    """
    root = (passfile_root or "").strip().rstrip("/")
    if not root:
        return
    if container == root or container.startswith(root + "/"):
        raise ValidationError(
            f"该路径位于仓库密码文件目录内，不可浏览或选作仓库路径：{container}",
            data={"path": container, "denied_root": root},
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
    **只保留目录**（owner 2026-10-24：WSL 把 init 二进制 bind mount 到 ``/init``，
    /proc/mounts 里大量文件/socket 型挂载——给二进制文件打「挂载点」徽章是纯噪声，
    备份场景里挂载点徽章只对数据卷有意义）。
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
        # 文件/socket 型挂载点剔除（/init、/tmp/.X11-unix 等）；is_dir 对无权限目录仍为 True
        if not Path(target).is_dir():
            continue
        mounts.add(target)
    return mounts
