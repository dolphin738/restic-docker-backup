"""密钥中性化回归（A 方案）：``keys/`` 是 restic 仓库唯一的明文文件。

分层断言：
- restic 拼装：``key add`` 带中性 ``--user`` / ``--host``；``--key-hint`` 落全局位置
  （verb 之前，删旧 key 时强制当前 key 指向新 key）；非法 key-hint 被拒；
- ``parse_key_list``：解析 ``restic key list --json``（cmd_key.go keyInfo 字段），
  空/非法输入不抛异常；
- ``neutralize_repo_key``：仅处理唯一 key、幂等（已中性化跳过）、任一步失败返回 False
  且不抛（不能把已成功的 init 反转成失败）；
- ``classify_keys``：只读可见性的展示模型（短 id / 中性判定 / overall 四态），
  且**不得**把 ``data`` 与 KDF 参数带进返回值。
"""

from __future__ import annotations

import json

import pytest

from app.errors import ValidationError
from app.services.repo_keys import (
    KEY_OVERALL_EXPOSING,
    KEY_OVERALL_MIXED,
    KEY_OVERALL_NEUTRALIZED,
    KEY_OVERALL_UNKNOWN,
    NEUTRAL_KEY_HOST,
    NEUTRAL_KEY_USER,
    classify_keys,
    neutralize_repo_key,
    parse_key_list,
)
from app.services.restic import ProbeResult, ResticCmd, WhitelistArgs

_RESTIC = ResticCmd()


# ---------------------------------------------------------------- restic 拼装


def test_key_add_argv_carries_neutral_identity() -> None:
    argv = _RESTIC.build(
        "key",
        args=WhitelistArgs(
            json_output=False,
            key_target="add",
            key_user=NEUTRAL_KEY_USER,
            key_host=NEUTRAL_KEY_HOST,
            new_password_file="/tmp/pw",
        ),
    )
    assert argv[argv.index("--user") + 1] == NEUTRAL_KEY_USER
    assert argv[argv.index("--host") + 1] == NEUTRAL_KEY_HOST
    assert "--new-password-file" in argv
    assert "/tmp/pw" in argv  # 口令只落路径（S3）


def test_key_hint_is_global_flag_before_verb() -> None:
    argv = _RESTIC.build(
        "key",
        args=WhitelistArgs(
            json_output=False,
            key_target="remove",
            key_id="a1b2c3d4",
            key_hint="e5f6a7b8",
        ),
    )
    assert argv.index("--key-hint") < argv.index("key")
    assert argv[argv.index("--key-hint") + 1] == "e5f6a7b8"
    assert "remove" in argv
    assert "a1b2c3d4" in argv


def test_key_hint_rejects_invalid_id() -> None:
    with pytest.raises(ValidationError):
        _RESTIC.build("key", args=WhitelistArgs(key_target="list", key_hint="NOT-HEX"))


# ---------------------------------------------------------------- parse_key_list


def test_parse_key_list_reads_restic_json() -> None:
    payload = [
        {
            "current": True,
            "id": "a1b2c3d4",
            "userName": "root",
            "hostName": "host1",
            "created": "2026-10-23 21:00:00",
        }
    ]
    keys = parse_key_list([json.dumps(payload)])
    assert len(keys) == 1
    assert keys[0]["id"] == "a1b2c3d4"
    assert keys[0]["userName"] == "root"


def test_parse_key_list_tolerates_empty_and_garbage() -> None:
    assert parse_key_list([]) == []
    assert parse_key_list(["not json"]) == []
    assert parse_key_list(['{"not": "a list"}']) == []


# ---------------------------------------------------------------- classify_keys（只读可见性）

# 真实 key id 是 64 位 hex（``keys/<id>`` 文件名）；短 id 只是展示缩略。
_FULL_ID_OLD = "a1b2c3d4" + "0" * 56
_FULL_ID_NEW = "e5f6a7b8" + "1" * 56


def _raw_key(
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


def test_classify_keys_neutralized_shortens_id() -> None:
    overall, items = classify_keys([_raw_key(_FULL_ID_NEW, user=NEUTRAL_KEY_USER, host=NEUTRAL_KEY_HOST, current=True)])
    assert overall == KEY_OVERALL_NEUTRALIZED
    assert len(items) == 1
    assert items[0]["id"] == "e5f6a7b8"  # 8 位与 restic key list 表格列一致
    assert items[0]["id_full"] == _FULL_ID_NEW
    assert items[0]["neutral"] is True
    assert items[0]["current"] is True


def test_classify_keys_exposing_and_mixed() -> None:
    exposing, _ = classify_keys([_raw_key(_FULL_ID_OLD, user="root", host="Wien-laptop")])
    assert exposing == KEY_OVERALL_EXPOSING

    mixed, items = classify_keys(
        [
            _raw_key(_FULL_ID_NEW, user=NEUTRAL_KEY_USER, host=NEUTRAL_KEY_HOST, current=True),
            _raw_key(_FULL_ID_OLD, user="alice", host="build-host"),
        ]
    )
    assert mixed == KEY_OVERALL_MIXED
    assert [item["neutral"] for item in items] == [True, False]


def test_classify_keys_unknown_when_empty() -> None:
    overall, items = classify_keys([])
    assert overall == KEY_OVERALL_UNKNOWN
    assert items == []


def test_classify_keys_drops_items_without_id() -> None:
    overall, items = classify_keys([{"userName": "root", "hostName": "h"}])
    assert overall == KEY_OVERALL_UNKNOWN  # 无法定位 = 不可展示
    assert items == []


def test_classify_keys_never_passes_through_kdf_or_master_key() -> None:
    """输出只含展示白名单 8 键：data/salt/N/r/p 绝不进 API（安全基线 §5.3）。"""
    raw = _raw_key(_FULL_ID_NEW, user=NEUTRAL_KEY_USER, host=NEUTRAL_KEY_HOST)
    raw.update({"data": "ciphertext", "salt": "c2FsdA==", "N": 32768, "r": 8, "p": 7})
    _overall, items = classify_keys([raw])
    assert set(items[0]) == {
        "id",
        "id_full",
        "user",
        "host",
        "created",
        "created_tz",
        "current",
        "neutral",
    }


def test_classify_keys_labels_created_with_subprocess_tz() -> None:
    """restic 的 created 无时区标记（按子进程 TZ 渲染）→ 必须带 TZ 标注，不做换算。"""
    raw = _raw_key(_FULL_ID_NEW, user=NEUTRAL_KEY_USER, host=NEUTRAL_KEY_HOST)
    _overall, items = classify_keys([raw], created_tz="Asia/Shanghai")
    assert items[0]["created"] == "2026-10-23 22:06:48"  # 原样，不转 UTC
    assert items[0]["created_tz"] == "Asia/Shanghai"

    _overall, default_items = classify_keys([raw])
    assert default_items[0]["created_tz"] == "UTC"  # 缺省 = settings.tz 默认值


# ---------------------------------------------------------------- 中性化流程


class _FakeRestic:
    """只 mock 执行层：``build`` 走真实白名单拼装，``run_json`` 按序返回预设结果。

    刻意不用「[verb, target] 两元素假 argv」——那样断言看不出 ``--json`` 缺失，
    而 ``--json`` 丢失会让 ``parse_key_list`` 静默返回空表（中性化被误判成
    「仓库没有 key」而跳过），是这条链路上最隐蔽的失效形态。
    """

    def __init__(self, results: list[ProbeResult]) -> None:
        self._results = list(results)
        self.calls: list[list[str]] = []

    def build(self, verb: str, *, args: WhitelistArgs, env_extras: dict[str, str] | None = None) -> list[str]:
        return _RESTIC.build(verb, args=args, env_extras=env_extras)

    async def run_json(self, argv: list[str], **kwargs: object) -> ProbeResult:
        del kwargs
        self.calls.append(list(argv))
        return self._results.pop(0)


def _key_target(argv: list[str]) -> str:
    """从真实 argv 里取 ``key`` 的子命令（用于断言命令序列）。"""
    return next((name for name in ("add", "list", "remove", "passwd") if name in argv), "")


_OLD = {
    "current": True,
    "id": "a1b2c3d4",
    "userName": "root",
    "hostName": "host1",
    "created": "2026-10-23 21:00:00",
}
_NEW = {
    "current": False,
    "id": "e5f6a7b8",
    "userName": NEUTRAL_KEY_USER,
    "hostName": NEUTRAL_KEY_HOST,
    "created": "2026-10-23 21:00:01",
}


def _p(exit_code: int = 0, lines: list[str] | None = None) -> ProbeResult:
    return ProbeResult(exit_code=exit_code, lines=lines or [])


async def test_neutralize_happy_path_adds_neutral_and_removes_old() -> None:
    fake = _FakeRestic(
        [
            _p(0, [json.dumps([_OLD])]),      # key list
            _p(0, ["saved new key"]),          # key add
            _p(0, [json.dumps([_OLD, _NEW])]),  # key list
            _p(0, ["removed key"]),            # key remove
        ]
    )
    ok = await neutralize_repo_key(
        fake, repo_id="default", env={"RESTIC_PASSWORD_FILE": "/pw"}, password_file="/pw"
    )
    assert ok is True
    assert [_key_target(call) for call in fake.calls] == ["list", "add", "list", "remove"]


async def test_key_list_argv_carries_json_flag() -> None:
    """护栏：``key list`` 缺 ``--json`` → 输出人类表格 → 解析空表 → 中性化静默跳过。"""
    fake = _FakeRestic([_p(0, [json.dumps([_NEW])])])
    await neutralize_repo_key(fake, repo_id="default", env={}, password_file="/pw")
    assert "--json" in fake.calls[0]
    assert fake.calls[0][-1] == "list"


async def test_neutralize_skips_when_already_neutral() -> None:
    fake = _FakeRestic([_p(0, [json.dumps([_NEW])])])
    ok = await neutralize_repo_key(fake, repo_id="default", env={}, password_file="/pw")
    assert ok is False
    assert len(fake.calls) == 1  # 只读一次 key list，不加不删


async def test_neutralize_skips_multiple_keys() -> None:
    fake = _FakeRestic([_p(0, [json.dumps([_OLD, _NEW])])])
    ok = await neutralize_repo_key(fake, repo_id="default", env={}, password_file="/pw")
    assert ok is False
    assert len(fake.calls) == 1


async def test_neutralize_aborts_when_add_fails() -> None:
    fake = _FakeRestic(
        [
            _p(0, [json.dumps([_OLD])]),
            _p(1, ["boom"]),
        ]
    )
    ok = await neutralize_repo_key(fake, repo_id="default", env={}, password_file="/pw")
    assert ok is False
    assert len(fake.calls) == 2  # 列表 + add，未触及 remove


async def test_neutralize_returns_false_when_remove_fails() -> None:
    fake = _FakeRestic(
        [
            _p(0, [json.dumps([_OLD])]),
            _p(0, ["saved new key"]),
            _p(0, [json.dumps([_OLD, _NEW])]),
            _p(1, ["refusing to remove key currently used"]),
        ]
    )
    ok = await neutralize_repo_key(fake, repo_id="default", env={}, password_file="/pw")
    assert ok is False
    assert len(fake.calls) == 4  # 走到 remove，失败被吞（函数不抛）
