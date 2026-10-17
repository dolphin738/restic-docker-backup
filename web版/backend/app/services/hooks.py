"""任务级前后钩子（批次 D C1，Duplicati ``RunScript`` 模块的 restic-web 等价物）。

语义约定（逐条对应计划批次 D C1）：

- ``pre_cmd``：任务进入 running 之后、dump/restic 阶段**之前**执行；失败即任务 failed
  （阻断后续阶段，避免「钩子没冻结应用就备份了半截文件」）；
- ``post_cmd``：任务**成功**终态之后执行（失败不跑）；失败同样计任务 failed
  （钩子是任务契约的一部分，静默吞掉等于让运维以为后续动作已完成）；
- **不经 shell**：命令串经 :func:`shlex.split` 切分为 argv 后走
  ``asyncio.create_subprocess_exec``（S2 红线：不经过 shell、无自由字符串透传）。
  代价如实标注：``|`` / ``>`` / ``&&`` 等 shell 语法不被解释——需要复合逻辑请写成
  脚本文件，把命令填为「脚本路径 + 参数」（部署指南 §7.2）；
- 输出（stdout+stderr 合并）追加进任务日志，SSE 与 E27 日志端点因而可见钩子痕迹；
  超 :data:`HOOK_OUTPUT_MAX_BYTES` 截断（留头，方便看命令行与首错）；
- 超时 kill（:data:`HOOK_TIMEOUT_SECONDS`），绝不让用户命令拖死整条任务链。
"""

from __future__ import annotations

import asyncio
import logging
import shlex
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from app.constants import (
    HOOK_COMMAND_MAX_LENGTH,
    HOOK_OUTPUT_MAX_BYTES,
    HOOK_TIMEOUT_SECONDS,
)
from app.errors import ValidationError
from app.services.restic import _assert_safe_value

logger = logging.getLogger(__name__)

# 钩子阶段（审计 detail / 日志前缀 / 前端文案三处共用）
HOOK_PHASE_PRE = "pre"
HOOK_PHASE_POST = "post"

# 钩子在任务日志里的行前缀（SSE 与 E27 原样展示；与 restic 输出行区分）
_HOOK_LOG_PREFIX = "[hook]"


@dataclass(frozen=True)
class HookOutcome:
    """单次钩子执行结果。"""

    exit_code: int
    output: str
    timed_out: bool = False
    parsed: bool = True

    @property
    def ok(self) -> bool:
        """钩子成功 = 正常解析且退出码 0 且未超时。"""
        return self.parsed and not self.timed_out and self.exit_code == 0

    def reason(self) -> str:
        """给任务 summary/审计用的中文失败原因（成功时为空串）。"""
        if not self.parsed:
            return "命令解析失败"
        if self.timed_out:
            return f"执行超时（>{HOOK_TIMEOUT_SECONDS}s）"
        if self.exit_code != 0:
            return f"退出码 {self.exit_code}"
        return ""


def split_command(text: str) -> tuple[str, ...] | None:
    """把命令串切分为 argv（不经 shell）；空串返回 None（= 未配置，不执行）。

    Raises:
        ValueError: 命令过长、解析失败（引号不闭合）、切分结果为空或含危险字符。
    """
    candidate = (text or "").strip()
    if not candidate:
        return None
    if len(candidate) > HOOK_COMMAND_MAX_LENGTH:
        raise ValueError(f"命令长度超过 {HOOK_COMMAND_MAX_LENGTH} 字符上限")
    # S2 同源校验（单一实现点：与 restic 参数值同款，禁 NUL/换行）——不经 shell 也不
    # 放松：日志与 SSE 会把命令行整条回显，含换行会让日志行断裂。
    # 统一收敛为 ValueError：钩子层不抛 API 错误类型，由调用方（TaskService 写侧 /
    # 执行期）各自翻译成 422 或任务 failed。
    try:
        _assert_safe_value(candidate, "hook_cmd")
    except ValidationError as exc:
        raise ValueError(str(exc)) from exc
    try:
        argv = tuple(shlex.split(candidate))
    except ValueError as exc:
        raise ValueError(f"命令解析失败：{exc}") from exc
    if not argv:
        raise ValueError("命令切分后为空")
    return argv


async def run_hook(
    *,
    command: str,
    phase: str,
    log_path: Path,
    timeout_seconds: float = HOOK_TIMEOUT_SECONDS,
) -> HookOutcome:
    """执行一条钩子命令并把痕迹追加进任务日志。

    Args:
        command: 命令串（空串 = 未配置，直接返回 ``ok`` 的空结果，调用方跳过）。
        phase: :data:`HOOK_PHASE_PRE` / :data:`HOOK_PHASE_POST`（仅用于日志与审计）。
        log_path: 任务日志文件路径（追加写，缺失则创建）。
        timeout_seconds: 超时（秒）；超时即 kill 并按失败处理。

    Returns:
        :class:`HookOutcome`；命令为空串时返回 ``HookOutcome(0, "", parsed=True)``
        的「未执行」结果（:attr:`HookOutcome.ok` 为 True，表示不阻断）。
    """
    try:
        argv = split_command(command)
    except ValueError as exc:
        logger.warning("钩子命令非法（phase=%s）：%s", phase, exc)
        _append_sync(log_path, [f"{_HOOK_LOG_PREFIX}[{phase}] 命令非法：{exc}"])
        return HookOutcome(exit_code=-1, output=str(exc), parsed=False)

    if argv is None:
        return HookOutcome(exit_code=0, output="")

    _append_sync(log_path, [f"{_HOOK_LOG_PREFIX}[{phase}] $ {shlex.join(argv)}"])
    exit_code, output, timed_out = await _exec(argv, timeout_seconds)
    if output.strip():
        _append_sync(log_path, [f"{_HOOK_LOG_PREFIX}[{phase}] {line}" for line in output.splitlines()])
    _append_sync(
        log_path,
        [f"{_HOOK_LOG_PREFIX}[{phase}] exit={exit_code}{'（超时已终止）' if timed_out else ''}"],
    )
    return HookOutcome(exit_code=exit_code, output=output, timed_out=timed_out)


async def _exec(argv: tuple[str, ...], timeout_seconds: float) -> tuple[int, str, bool]:
    """执行 argv（不经 shell）；超时 kill 并置 ``timed_out``。"""
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except (FileNotFoundError, PermissionError, OSError) as exc:
        return -1, f"无法执行：{exc}", False
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout_seconds)
    except TimeoutError:
        proc.kill()
        with suppress(Exception):
            await proc.wait()
        return -1, f"命令超时（>{timeout_seconds:g}s）", True
    text = (out or b"").decode("utf-8", errors="replace")
    if len(text) > HOOK_OUTPUT_MAX_BYTES:
        text = text[:HOOK_OUTPUT_MAX_BYTES] + "…(输出已截断)"
    return (proc.returncode if proc.returncode is not None else -1), text, False


def _append_sync(log_path: Path, lines: list[str]) -> None:
    """同步追加日志行（钩子输出量小，逐行 await 无必要；失败只记日志）。"""
    if not lines:
        return
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write("".join(f"{line}\n" for line in lines))
    except OSError as exc:  # 日志写失败不应打断钩子语义判定
        logger.warning("钩子日志写入失败：%s", exc)
