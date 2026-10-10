"""SseChannel（§5.1 E15 / §6.2）：日志回放 + 追加续流 + 15s 心跳 + 终态收流。

设计要点：
- **事件 id = 任务日志行号**（1 起），与 ``jobs.last_event_id``（§4.5 日志行偏移）同义，
  因此 ``Last-Event-ID`` 续传 == 从第 N+1 行开始回放；
- **字节偏移**是回放的实现细节：DB 只存行号（与文档一致），进程内缓存
  ``(job_id, line_no) → byte_offset`` 用于快速 seek，缓存未命中时退化为全文件逐行扫描
  （结果正确，仅多一次 O(n) 扫描）；
- 每行经 :class:`OutputParser` 解析（线程池调用，不阻塞事件循环）后决定事件类型：
  进度 → ``progress``、其余 → ``log``；终态时补发 ``done``/``error`` 并关闭流。
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from pathlib import Path

from app.constants import (
    SSE_HEARTBEAT_SECONDS,
    SSE_POLL_INTERVAL_SECONDS,
    TERMINAL_JOB_STATUSES,
)
from app.db.repositories.job_repo import Job, JobRepo
from app.services.parser import (
    FileRecord,
    LogLine,
    OutputParser,
    ProgressEvent,
    SnapshotRecord,
)


class SseChannel:
    """任务日志 → SSE 事件流的转换通道。"""

    def __init__(
        self,
        parser: OutputParser,
        jobs: JobRepo,
        *,
        heartbeat_seconds: float = SSE_HEARTBEAT_SECONDS,
        poll_interval: float = SSE_POLL_INTERVAL_SECONDS,
    ) -> None:
        """构造通道。

        Args:
            parser: 输出解析器（纯函数，放线程池调用）。
            jobs: 任务仓储（用于终态判定）。
            heartbeat_seconds: 空闲心跳间隔（秒）。
            poll_interval: 日志文件追加轮询间隔（秒）。
        """
        self._parser = parser
        self._jobs = jobs
        self._heartbeat = heartbeat_seconds
        self._poll = poll_interval
        self._offset_cache: dict[tuple[str, int], int] = {}

    async def stream(self, job: Job, last_event_id: int = 0) -> AsyncIterator[str]:
        """产出 SSE 文本块（已格式化，可直接写入响应体）。

        Args:
            job: 任务对象（路由侧已确认存在）。
            last_event_id: 客户端已收到的最后一个事件 id；0 表示从头回放。

        Yields:
            形如 ``id: n\\nevent: log\\ndata: {...}\\n\\n`` 的文本块，
            或心跳注释行 ``: keep-alive\\n\\n``。
        """
        log_path = Path(job.log_path)
        start_line = max(0, int(last_event_id))
        offset, line_no = self._resume(log_path, job.id, start_line)
        last_activity = time.monotonic()
        snapshot_id = job.snapshot_id
        pending = ""

        with log_path.open("rb") as handle:
            handle.seek(offset)
            while True:
                chunk = handle.read()
                if chunk:
                    pending += chunk.decode("utf-8", errors="replace")
                    complete, pending = _split_complete(pending)
                    for text in complete:
                        line_no += 1
                        payload = await asyncio.to_thread(self._parser.parse_line, text)
                        block = self._render(line_no, payload)
                        if isinstance(payload, SnapshotRecord) and payload.snapshot_id:
                            snapshot_id = payload.snapshot_id
                        if block:
                            last_activity = time.monotonic()
                            yield block
                        self._offset_cache[(job.id, line_no)] = handle.tell() - len(pending.encode("utf-8"))

                current = await self._jobs.get(job.id)
                status = current.status if current else job.status
                if current is not None:
                    snapshot_id = snapshot_id or current.snapshot_id
                if status in TERMINAL_JOB_STATUSES:
                    yield _frame(
                        "error" if status in ("failed", "interrupted") else "done",
                        {
                            "status": status,
                            "exit_code": current.exit_code if current else None,
                            "snapshot_id": snapshot_id,
                            "skipped_count": current.skipped_count if current else 0,
                            "bytes_done": current.bytes_done if current else None,
                            "files_done": current.files_done if current else None,
                            "summary": current.summary if current else None,
                        },
                        line_no + 1,
                    )
                    return

                if time.monotonic() - last_activity >= self._heartbeat:
                    last_activity = time.monotonic()
                    yield ": keep-alive\n\n"
                await asyncio.sleep(self._poll)

    # ---------------------------------------------------------------- 内部实现

    def _resume(self, log_path: Path, job_id: str, start_line: int) -> tuple[int, int]:
        """把"行号游标"换算成"字节偏移"，缓存未命中时逐行扫描。

        Args:
            log_path: 日志文件路径。
            job_id: 任务 id（缓存维度）。
            start_line: 已消费的行数（即接下来从第 start_line+1 行开始）。

        Returns:
            ``(byte_offset, line_no)``。
        """
        if start_line <= 0:
            return 0, 0
        cached = self._offset_cache.get((job_id, start_line))
        if cached is not None:
            return cached, start_line
        if not log_path.exists():
            return 0, 0
        offset = 0
        line_no = 0
        with log_path.open("rb") as handle:
            for raw in handle:
                offset += len(raw)
                line_no += 1
                if line_no >= start_line:
                    break
        self._offset_cache[(job_id, line_no)] = offset
        return offset, line_no

    def _render(self, line_no: int, payload: object) -> str | None:
        """把解析结果渲染为 SSE 文本块；不产生事件的返回 None。"""
        if isinstance(payload, ProgressEvent):
            return _frame(
                "progress",
                {
                    "event_id": line_no,
                    "percent": payload.percent,
                    "bytes_done": payload.bytes_done,
                    "total_bytes": payload.total_bytes,
                    "files_done": payload.files_done,
                    "total_files": payload.total_files,
                    "skipped_delta": payload.skipped_delta,
                },
                line_no,
            )
        if isinstance(payload, FileRecord):
            return _frame("log", {"event_id": line_no, "path": payload.path, "name": payload.name}, line_no)
        if isinstance(payload, LogLine):
            return _frame("log", {"event_id": line_no, "text": payload.text}, line_no)
        # SnapshotRecord 只用于回填 done 事件中的 snapshot_id，不单独发事件
        return None


def _split_complete(buffer: str) -> tuple[list[str], str]:
    """切出已完整结束的行，剩余半行留在缓冲区。

    刻意**保留空行**：SSE 事件 id 必须严格等于日志文件行号，
    否则 ``jobs.last_event_id`` 与客户端 ``Last-Event-ID`` 会错位；
    空行由 :meth:`OutputParser.parse_line` 返回 None，只推进行号、不发事件。
    """
    if "\n" not in buffer:
        return [], buffer
    *lines, remainder = buffer.split("\n")
    return [line.rstrip("\r") for line in lines], remainder


def _frame(event: str, data: dict, event_id: int | None = None) -> str:
    """按 SSE 规范格式化单个事件。"""
    lines: list[str] = []
    if event_id is not None:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event}")
    lines.append("data: " + json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    return "\n".join(lines) + "\n\n"
