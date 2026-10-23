"""只读密钥身份端点（``GET /api/repos/{repo_id}/keys``）。

存在理由：中性化是「静默失败、不报错」的设计（失败只记日志、绝不翻转 init 终态），
没有可见入口就无法确认它到底生效没有（《云端仓库安全基线》§4.3 / §5.5）。

本文件锁定契约（§5）：
- 200 + ``overall`` 四态（neutralized / exposing / mixed / unknown）；
- **只回展示字段**：``data``（master key 密文）与 KDF 参数 ``salt/N/r/p`` 一律不出现；
- 探测失败回 200 + ``probe_error``（不 5xx、不留空白）；
- 仓库地址未配置时**不**调 restic；
- 仓库不存在 404；未登录 401。
"""

from __future__ import annotations

import json

from conftest import finish_bootstrap

from app.services.restic import ProbeResult

# 真实 key id = 64 位 hex（keys/<id> 文件名）；接口回 8 位短 id + 完整 id
_FULL_ID_NEUTRAL = "e5f6a7b8" + "1" * 56
_FULL_ID_REAL = "a1b2c3d4" + "0" * 56


def _key_payload(
    key_id: str,
    *,
    user: str,
    host: str,
    current: bool = False,
    created: str = "2026-10-23 22:06:48",
) -> dict[str, object]:
    return {
        "current": current,
        "id": key_id,
        "userName": user,
        "hostName": host,
        "created": created,
    }


class _RunJsonRecorder:
    """restic 只读探测替身：记录 argv，恒返回预置结果。"""

    def __init__(self, result: ProbeResult) -> None:
        self.result = result
        self.calls: list[list[str]] = []

    async def __call__(self, argv: list[str], **kwargs: object) -> ProbeResult:
        del kwargs
        self.calls.append(list(argv))
        return self.result


def _json_result(keys: list[dict[str, object]], exit_code: int = 0) -> ProbeResult:
    return ProbeResult(exit_code=exit_code, lines=[json.dumps(keys)])


async def _seed_repo(ns, repo_id: str, *, url: str | None = "/tmp/repo") -> None:
    await ns.app.state.repos_repo.create(repo_id, f"仓库 {repo_id}", url=url)


async def test_repo_keys_neutralized_exposes_display_fields_only(app_client):
    ns = app_client
    await finish_bootstrap(ns)
    await _seed_repo(ns, "k1")

    # 输入里刻意带上 data/salt/N/r/p —— 接口不得透传（安全边界）
    raw = _key_payload(_FULL_ID_NEUTRAL, user="svc-backup", host="repo", current=True)
    raw.update({"data": "ciphertext", "salt": "c2FsdA==", "N": 32768, "r": 8, "p": 7})
    recorder = _RunJsonRecorder(_json_result([raw]))
    ns.app.state.restic.run_json = recorder  # type: ignore[assignment]

    resp = await ns.client.get("/api/repos/k1/keys")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["repo_id"] == "k1"
    assert body["overall"] == "neutralized"
    assert body["probe_error"] is None
    assert len(body["keys"]) == 1
    key = body["keys"][0]
    assert key["id"] == "e5f6a7b8"
    assert key["id_full"] == _FULL_ID_NEUTRAL
    assert key["user"] == "svc-backup"
    assert key["host"] == "repo"
    assert key["neutral"] is True
    assert key["current"] is True
    # 只回 8 个展示字段：data / salt / N / r / p 一个都不许出现
    assert set(key) == {
        "id",
        "id_full",
        "user",
        "host",
        "created",
        "created_tz",
        "current",
        "neutral",
    }
    # created 是 restic 按子进程 TZ 渲染的无时区字符串 → 必须带 TZ 标注
    assert key["created_tz"] == "UTC"
    # argv 契约：key list 且带 --json（缺 --json 会退化成人类表格 → 解析空表）
    assert recorder.calls and recorder.calls[0][-1] == "list"
    assert "--json" in recorder.calls[0]


async def test_repo_keys_exposing_when_identity_not_neutral(app_client):
    ns = app_client
    await finish_bootstrap(ns)
    await _seed_repo(ns, "k2")
    recorder = _RunJsonRecorder(
        _json_result([_key_payload(_FULL_ID_REAL, user="root", host="Wien-laptop", current=True)])
    )
    ns.app.state.restic.run_json = recorder  # type: ignore[assignment]

    resp = await ns.client.get("/api/repos/k2/keys")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["overall"] == "exposing"
    assert body["keys"][0]["user"] == "root"
    assert body["keys"][0]["neutral"] is False


async def test_repo_keys_mixed_when_old_key_left_behind(app_client):
    ns = app_client
    await finish_bootstrap(ns)
    await _seed_repo(ns, "k3")
    recorder = _RunJsonRecorder(
        _json_result(
            [
                _key_payload(_FULL_ID_NEUTRAL, user="svc-backup", host="repo", current=True),
                _key_payload(_FULL_ID_REAL, user="alice", host="build-host"),
            ]
        )
    )
    ns.app.state.restic.run_json = recorder  # type: ignore[assignment]

    resp = await ns.client.get("/api/repos/k3/keys")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["overall"] == "mixed"
    assert [key["neutral"] for key in body["keys"]] == [True, False]


async def test_repo_keys_probe_failure_is_visible_not_5xx(app_client):
    """探测失败必须回 200 + 原因（同 E30 口径），不能 5xx、不能空白。"""
    ns = app_client
    await finish_bootstrap(ns)
    await _seed_repo(ns, "k4")
    recorder = _RunJsonRecorder(
        ProbeResult(exit_code=1, lines=["Fatal: unable to open config file: wrong password"])
    )
    ns.app.state.restic.run_json = recorder  # type: ignore[assignment]

    resp = await ns.client.get("/api/repos/k4/keys")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["overall"] == "unknown"
    assert body["keys"] == []
    assert body["probe_error"]

    # 未初始化/空 key 列表同样要给出原因（而不是「已中性化」的假绿）
    recorder_empty = _RunJsonRecorder(ProbeResult(exit_code=0, lines=["[]"]))
    ns.app.state.restic.run_json = recorder_empty  # type: ignore[assignment]
    resp = await ns.client.get("/api/repos/k4/keys")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["overall"] == "unknown"
    assert body["probe_error"]


async def test_repo_keys_skips_restic_when_url_missing(app_client):
    ns = app_client
    await finish_bootstrap(ns)
    await _seed_repo(ns, "k5", url=None)
    recorder = _RunJsonRecorder(_json_result([]))
    ns.app.state.restic.run_json = recorder  # type: ignore[assignment]

    resp = await ns.client.get("/api/repos/k5/keys")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["overall"] == "unknown"
    assert "未配置" in body["probe_error"]
    assert recorder.calls == []  # 地址都没有，不必再 spawn restic


async def test_repo_keys_repo_not_found(app_client):
    ns = app_client
    await finish_bootstrap(ns)
    resp = await ns.client.get("/api/repos/k9/keys")
    assert resp.status_code == 404


async def test_repo_keys_requires_session(app_client):
    resp = await app_client.client.get("/api/repos/k1/keys")
    assert resp.status_code == 401
