"""API 集成：E15 SSE 流——终态不回放全量、事件 id 严格等于日志行号（主理人指定断言）。

日志样本设计（6 行，含刻意空行与 summary）：
  行1 status   → progress 帧
  行2 （空行）  → 不发帧（只推进行号）
  行3 error    → progress 帧（skipped_delta=1）
  行4 非 JSON  → log 帧
  行5 summary  → 不发帧（仅回填 snapshot_id）
  终态 done 帧 → id = 6 = last_event_id + 1
"""

from __future__ import annotations

import json

import pytest
from conftest import finish_bootstrap

from app.utils.ulid import new_job_id

LOG_LINES = [
    '{"message_type":"status","percent_done":0.5,"total_files":2,"files_done":1,"total_bytes":100,"bytes_done":50}',
    "",
    '{"message_type":"error","during":"archival","item":"/host/etc/shadow","error":"permission denied"}',
    "restic: plain text log line",
    '{"message_type":"summary","total_bytes_processed":100,"snapshot_id":"a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2","snapshot_time":"2026-10-10T01:38:00Z"}',
]


async def _make_terminal_job(ns, status: str = "success"):
    """落一条终态任务并写入 5 行日志，last_event_id=5。"""
    settings = ns.app.state.settings
    settings.jobs_dir.mkdir(parents=True, exist_ok=True)
    job_id = new_job_id()
    log_path = settings.jobs_dir / f"{job_id}.log"
    log_path.write_text("\n".join(LOG_LINES) + "\n", encoding="utf-8")
    await ns.app.state.job_repo.create(
        job_id=job_id,
        job_type="backup",
        trigger="manual",
        log_path=str(log_path),
        status=status,
    )
    await ns.app.state.job_repo.finalize(
        job_id,
        status,
        exit_code=0 if status == "success" else 1,
        summary="done",
        last_event_id=len(LOG_LINES),
        bytes_done=50,
        files_done=1,
        skipped_count=1,
    )
    # 真实链路中 snapshot_id 由 JobRunner 从 summary 解析后回填 DB
    await ns.app.state.db.write(
        "UPDATE jobs SET snapshot_id = ? WHERE id = ?",
        ("a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2", job_id),
    )
    return job_id


async def _collect_sse(client, path, **kwargs) -> tuple[dict, list[dict]]:
    """消费 SSE 流直到服务端关闭，返回 (响应头, [ {id,event,data} ])。"""
    headers_out: dict = {}
    frames: list[dict] = []
    current: dict = {}
    async with client.stream("GET", path, **kwargs) as resp:
        headers_out = dict(resp.headers)
        resp.raise_for_status()
        async for line in resp.aiter_lines():
            if not line:
                if current:
                    frame = dict(current)
                    if "data" in frame:
                        frame["data"] = json.loads(frame["data"])
                    frames.append(frame)
                    current = {}
                continue
            if line.startswith(":"):
                continue  # keep-alive 注释行
            key, _, value = line.partition(": ")
            current[key] = value
    if current:
        frame = dict(current)
        if "data" in frame:
            frame["data"] = json.loads(frame["data"])
        frames.append(frame)
    return headers_out, frames


async def test_sse_terminal_job_with_cursor_no_full_replay(app_client):
    """断言①：终态任务带 last_event_id 开流 → 只收 1 帧（done），无 log/progress 回放。"""
    ns = app_client
    await finish_bootstrap(ns)
    job_id = await _make_terminal_job(ns)

    detail = (await ns.client.get(f"/api/jobs/{job_id}")).json()
    assert detail["last_event_id"] == 5

    _, frames = await _collect_sse(
        ns.client, f"/api/jobs/{job_id}/stream", params={"last_event_id": detail["last_event_id"]}
    )
    assert len(frames) == 1, frames
    assert frames[0]["event"] == "done"
    assert frames[0]["id"] == "6"
    assert frames[0]["data"]["status"] == "success"
    assert frames[0]["data"]["snapshot_id"] == "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2"
    assert frames[0]["data"]["skipped_count"] == 1


async def test_sse_event_ids_equal_log_line_numbers(app_client):
    """断言②：从 0 消费到终态，每帧 id 严格等于日志行号，最大 id = last_event_id + 1。

    空行（行2）与 summary（行5）刻意不发帧但推进行号（_split_complete 保留空行设计）。
    """
    ns = app_client
    await finish_bootstrap(ns)
    job_id = await _make_terminal_job(ns)

    _, frames = await _collect_sse(ns.client, f"/api/jobs/{job_id}/stream")

    ids = [int(frame["id"]) for frame in frames]
    assert ids == [1, 3, 4, 6]  # 行2/行5 无帧；done 帧 = 5 + 1
    assert ids == sorted(ids)  # 严格递增

    by_id = {int(f["id"]): f for f in frames}
    assert by_id[1]["event"] == "progress"
    assert by_id[1]["data"]["bytes_done"] == 50
    assert by_id[3]["event"] == "progress"
    assert by_id[3]["data"]["skipped_delta"] == 1
    assert by_id[4]["event"] == "log"
    assert by_id[4]["data"]["text"] == "restic: plain text log line"
    assert by_id[6]["event"] == "done"

    detail = (await ns.client.get(f"/api/jobs/{job_id}")).json()
    assert max(ids) == detail["last_event_id"] + 1


async def test_sse_last_event_id_header_supported(app_client):
    """Last-Event-ID 请求头与 ?last_event_id= 等价（浏览器重连路径）。"""
    ns = app_client
    await finish_bootstrap(ns)
    job_id = await _make_terminal_job(ns)

    _, frames = await _collect_sse(
        ns.client, f"/api/jobs/{job_id}/stream", headers={"Last-Event-ID": "5"}
    )
    assert len(frames) == 1
    assert frames[0]["event"] == "done"


async def test_sse_response_headers(app_client):
    ns = app_client
    await finish_bootstrap(ns)
    job_id = await _make_terminal_job(ns)

    headers, _ = await _collect_sse(ns.client, f"/api/jobs/{job_id}/stream")
    assert headers["content-type"].startswith("text/event-stream")
    assert headers.get("x-accel-buffering") == "no"
    assert headers.get("cache-control") == "no-cache"


async def test_sse_failed_job_emits_error_frame(app_client):
    """failed 终态发 error 帧（而非 done）。"""
    ns = app_client
    await finish_bootstrap(ns)
    job_id = await _make_terminal_job(ns, status="failed")

    _, frames = await _collect_sse(ns.client, f"/api/jobs/{job_id}/stream", params={"last_event_id": "5"})
    assert len(frames) == 1
    assert frames[0]["event"] == "error"
    assert frames[0]["data"]["status"] == "failed"


async def test_sse_requires_session(app_client):
    ns = app_client
    job_id = await _make_terminal_job(ns)
    resp = await ns.client.get(f"/api/jobs/{job_id}/stream")
    assert resp.status_code == 401


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Bug：日志文件不存在的任务（如 scheduled 冲突落下的 cancelled 跳过任务，"
        "log_path=jobs/<type>-skipped.log 从未被创建）请求 SSE 时，SseChannel.stream "
        "(app/services/sse.py:79) 直接 open('rb') 抛 FileNotFoundError → 流中途 500；"
        "预期应与 E27 一致降级为空日志并只发终态帧"
    ),
)
async def test_sse_missing_log_file_graceful(app_client):
    ns = app_client
    await finish_bootstrap(ns)
    job_id = new_job_id()
    await ns.app.state.job_repo.create(
        job_id=job_id,
        job_type="backup",
        trigger="scheduled",
        log_path=str(ns.app.state.settings.jobs_dir / f"{job_id}.log"),  # 从未写入
        status="cancelled",
    )
    _, frames = await _collect_sse(ns.client, f"/api/jobs/{job_id}/stream")
    assert len(frames) == 1
    assert frames[0]["event"] == "done"
