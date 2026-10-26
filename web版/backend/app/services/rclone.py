"""rclone 只读探测（A1）：conf 存在性 + remote 名清单。

运行模型（R-022 §3 生命周期结论，owner 裁定）：restic 原生 ``rclone:`` 后端按仓库
命令**按需拉起** ``rclone serve restic --stdio`` 子进程、命令结束即退出——不常驻、
不进 asyncio 事件循环，同仓库任务互斥天然覆盖。本模块不做 serve，只做
``listremotes`` 只读探测：

- **Q17 档位（只读托管）**：``rclone.conf`` 由宿主机 ``rclone config`` 生成、
  compose 挂载（**必须 :rw**——网盘 OAuth token 过期时 rclone 回写 conf，只读挂载
  = 授权静默失效）。本模块只读探测，**不生成、不编辑、不展示凭据**（conf 内
  OAuth token 永不进 API，与 cred.* 同口径）；
- 用途一：rclone 仓库 URL 保存前的 remote 存在性校验（Q20，不在册 422）；
- 用途二：前端展示可用 remote 清单（``GET /api/rclone/remotes``）。

子进程执行镜像 ``dump.py`` 模式：``create_subprocess_exec`` 直接 exec（不经 shell）、
合并 stdout/stderr、超时 kill。
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from app.constants import RCLONE_PROBE_TIMEOUT_SECONDS, SUBPROCESS_LOOP_HINT
from app.settings import Settings


@dataclass(frozen=True)
class RcloneRemotes:
    """一次 ``rclone listremotes`` 探测的结果。

    Attributes:
        available: rclone 二进制可用且探测执行成功（与 conf 是否存在无关）。
        conf_present: rclone 配置文件在文件系统中真实存在（只读判定）。
        conf_path: 生效的 conf 路径（``RCLONE_CONFIG`` 设置值）；未设置时为 None
            （rclone 用自身默认 ``$HOME/.config/rclone/rclone.conf``）。
        remotes: 远端名清单（已去掉行尾 ``:``）；探测失败时为空。
        reason: 不可用/失败原因（``available=True`` 时为 None；原因明示，禁止静默）。
    """

    available: bool
    conf_present: bool
    conf_path: str | None
    remotes: tuple[str, ...]
    reason: str | None


def resolve_conf_path(settings: Settings) -> str:
    """生效的 conf 路径（探测与配置管理**共用**这一处，避免第二份路径逻辑）。

    Args:
        settings: 运行期配置（``rclone_config`` 即 ``RCLONE_CONFIG``）。

    Returns:
        ``settings.rclone_config``；未设置时回落 rclone 自身默认位置
        ``$HOME/.config/rclone/rclone.conf``（主机档即宿主机真实路径，容器档
        由 compose 挂载目录提供——两种形态共用同一解析，无形态分支）。
    """
    if settings.rclone_config:
        return settings.rclone_config
    home = os.environ.get("HOME") or os.path.expanduser("~")
    return str(Path(home) / ".config" / "rclone" / "rclone.conf")


async def exec_rclone(
    argv: Sequence[str], timeout: float = RCLONE_PROBE_TIMEOUT_SECONDS
) -> tuple[int, str]:
    """执行 rclone 命令（不经 shell、stdin 置 DEVNULL、超时 kill）。

    探测（``listremotes``）与配置管理（``config`` 的 create / update / delete /
    dump 子命令）共用同一执行器，避免第二份子进程实现（§3.1 v1.13 重复实现条款）。

    Args:
        argv: 命令与参数（**不经 shell**，故无 shell 注入面）。
        timeout: 超时秒数，超时 kill 并抛 ``TimeoutError``。
        stdin 置 ``DEVNULL``：任何交互式提示（缺参数时 rclone 会追问）立即取其
        EOF 而报错退出，**不会挂住子进程**。

    Returns:
        ``(exit_code, 合并输出)``。

    Raises:
        FileNotFoundError: 未找到 rclone 二进制。
        TimeoutError: 超时。
        NotImplementedError: 事件循环不支持子进程（Windows + SelectorEventLoop）。
    """
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        stdin=asyncio.subprocess.DEVNULL,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        proc.kill()
        with suppress(Exception):
            await proc.wait()
        raise
    text = (out or b"").decode("utf-8", errors="replace")
    return proc.returncode if proc.returncode is not None else -1, text


class RcloneProbe:
    """``rclone listremotes`` 只读探测器（无状态，可跨请求复用）。"""

    def __init__(self, settings: Settings) -> None:
        """构造探测器。

        Args:
            settings: 运行期配置（读取 ``rclone_config`` 回报 conf 路径）。
        """
        self._settings = settings

    async def list_remotes(self) -> RcloneRemotes:
        """执行一次 ``rclone listremotes``（超时/缺二进制/事件循环不支持子进程均降级为 available=False）。

        Returns:
            :class:`RcloneRemotes`——任何失败路径都带可操作 ``reason``，不抛异常
            （调用方（URL 校验/端点）自行决定 422 还是展示）。
        """
        conf_path = self._settings.rclone_config
        conf_present = self._conf_present(conf_path)
        try:
            exit_code, output = await self._exec(["rclone", "listremotes"])
        except FileNotFoundError:
            return RcloneRemotes(
                available=False,
                conf_present=conf_present,
                conf_path=conf_path,
                remotes=(),
                reason="容器内未找到 rclone 可执行文件（镜像应已包含；请检查镜像版本）",
            )
        except TimeoutError:
            return RcloneRemotes(
                available=False,
                conf_present=conf_present,
                conf_path=conf_path,
                remotes=(),
                reason=f"rclone 探测超时（>{RCLONE_PROBE_TIMEOUT_SECONDS:g}s）",
            )
        except NotImplementedError:
            # Windows + uvicorn --reload：事件循环为 SelectorEventLoop，不支持子进程
            # （2026-10-19 真机实证：裸 --reload 启动时本探测 500）。降级为可操作原因，
            # 与缺二进制/超时同口径，绝不裸 500。
            return RcloneRemotes(
                available=False,
                conf_present=conf_present,
                conf_path=conf_path,
                remotes=(),
                reason=SUBPROCESS_LOOP_HINT,
            )
        if exit_code != 0:
            return RcloneRemotes(
                available=False,
                conf_present=conf_present,
                conf_path=conf_path,
                remotes=(),
                reason=output.strip()[:200] or f"rclone listremotes 失败（退出码 {exit_code}）",
            )
        remotes = tuple(
            line.strip().rstrip(":").strip() for line in output.splitlines() if line.strip()
        )
        reason = None
        if not remotes and not conf_present:
            # conf 缺失时 rclone 静默返回空清单——这里补一条可操作原因（Q17 部署指引入口）
            reason = "未检测到 rclone 配置文件（请在宿主机 rclone config 生成后挂载到容器）"
        return RcloneRemotes(
            available=True,
            conf_present=conf_present,
            conf_path=conf_path,
            remotes=remotes,
            reason=reason,
        )

    @staticmethod
    def _conf_present(conf_path: str | None) -> bool:
        """conf 文件存在性（只读判定；未设置路径时回落 rclone 默认位置）。"""
        if conf_path:
            return Path(conf_path).is_file()
        home = os.environ.get("HOME") or os.path.expanduser("~")
        return (Path(home) / ".config" / "rclone" / "rclone.conf").is_file()

    async def _exec(self, argv: list[str]) -> tuple[int, str]:
        """执行外部命令（不经 shell），超时 kill；返回 ``(exit_code, 合并输出)``。"""
        return await exec_rclone(argv)
